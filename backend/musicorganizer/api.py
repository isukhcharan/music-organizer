"""Local HTTP API over the core (the React UI talks to this; the CLI uses the same core).

Security model: it is a single-user local tool.
  * the server binds to 127.0.0.1 (see ``serve``); Host and Origin headers are checked so a web page you
    visit cannot drive it (DNS rebinding / cross-site requests),
  * nothing is moved unless the request says ``confirm: true``,
  * review decisions pick a candidate by *index into the server's stored list*, never client-supplied data,
  * the folder browser only lists directories.
"""
from __future__ import annotations

import csv
import io
import json
import string
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .cleanup import apply_cleanup, default_trash, plan_cleanup
from .journal import Journal
from .repair import TrackGenreLookup, apply_changes, itunes_artist_genre, plan_repair, scan_folder
from .organizer import apply_plan, plan_moves, undo_job
from .pipeline import discover_audio, match_library
from .resolver import DEFAULT_CHOICES, FIELDS, manual_destination, plan_manual, provider_values, resolve_values
from .store import REVIEW_STATUSES, Store
from .tags import read_local

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


# -- request models ---------------------------------------------------------------------------------
class NewJob(BaseModel):
    input_dir: str
    output_dir: str
    provider: str = "auto"


class Confirm(BaseModel):
    confirm: bool = False


class ApplyBody(BaseModel):
    confirm: bool = False
    mode: str = "move"                  # "move" or "copy" (copy leaves the originals untouched)


class ReviewBody(BaseModel):
    source: str
    candidate_index: int = Field(ge=0)
    choices: dict[str, dict] = Field(default_factory=dict)


class SourceBody(BaseModel):
    source: str


class RepairBody(BaseModel):
    folder: str
    itunes: bool = True                 # per-track genre lookups (slow: ~3 s per track, cached)
    artist_fallback: bool = False       # blanket artist-level genres (less accurate, off by default)


class CleanupBody(BaseModel):
    confirm: bool = False
    paths: list[str] = Field(default_factory=list)


class PickBody(BaseModel):
    initial: str = ""
    title: str = "Choose a folder"


# Runs in its own process: opens the native folder dialog and prints the chosen path (exit 3 = no GUI toolkit).
_PICK_SCRIPT = """
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    sys.exit(3)
try:
    root = tk.Tk()
except Exception:
    sys.exit(3)
root.withdraw()
root.attributes("-topmost", True)
print(filedialog.askdirectory(initialdir=sys.argv[1] or None, mustexist=True, title=sys.argv[2]) or "")
"""


# -- helpers ----------------------------------------------------------------------------------------
def _is_local(value: str | None) -> bool:
    if not value:
        return True
    host = urlparse(value if "//" in value else f"//{value}").hostname
    return host in LOCAL_HOSTS


def _candidates(row: dict) -> list[dict]:
    raw = row.get("candidates") or "[]"
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        out = []
    return out[:8]


_DEFAULT = object()


def create_app(db_path: Path | str, provider_factory: Callable[[str], object] | None = None,
               trash=_DEFAULT, genre_lookup=_DEFAULT) -> FastAPI:
    """``provider_factory(name)`` returns a Provider; tests pass a fake, the real app builds YouTube Music etc.
    ``trash(path)`` sends a folder to the Recycle Bin (default: send2trash, or None if it is not installed)."""
    if provider_factory is None:
        from .cli import build_provider as provider_factory  # noqa: PLW0621
    trash_fn = default_trash() if trash is _DEFAULT else trash
    repairs: dict[str, dict] = {}

    app = FastAPI(title="Music Organizer", docs_url="/api/docs", openapi_url="/api/openapi.json")
    progress: dict[str, dict] = {}
    locks: dict[str, threading.Lock] = {}

    def stores() -> tuple[Store, Journal]:
        # one connection per request/thread: sqlite connections must not be shared across threads
        return Store(db_path), Journal(db_path)

    def lock_for(job_id: str) -> threading.Lock:
        return locks.setdefault(job_id, threading.Lock())

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if not _is_local(request.headers.get("host")) or not _is_local(request.headers.get("origin")):
            return JSONResponse({"detail": "local access only"}, status_code=403)
        return await call_next(request)

    def job_or_404(store: Store, job_id: str) -> dict:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, f"unknown job {job_id}")
        return job

    # -- status / folders ---------------------------------------------------------------------------
    @app.get("/api/status")
    def status():
        return {"ok": True, "version": "0.1.0", "platform": sys.platform}

    @app.get("/api/fs/list")
    def list_dirs(path: str = Query("")):
        """Directory browser for choosing input/output folders (directories only, no file names)."""
        if not path:
            if sys.platform == "win32":
                roots = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            else:
                roots = ["/"]
            return {"path": "", "parent": None, "dirs": [{"name": r, "path": r} for r in roots]}
        p = Path(path)
        if not p.is_dir():
            raise HTTPException(400, "not a folder")
        dirs = []
        try:
            for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
                try:
                    if child.is_dir() and not child.name.startswith("."):
                        dirs.append({"name": child.name, "path": str(child)})
                except OSError:
                    continue
        except OSError as exc:
            raise HTTPException(403, f"cannot read folder: {exc}")
        audio = sum(1 for c in p.iterdir() if c.is_file() and c.suffix.lower() in {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".wma", ".aac"}) if p.is_dir() else 0
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "", "dirs": dirs, "audio_files_here": audio}

    @app.post("/api/fs/pick")
    def pick_folder(body: PickBody):
        """Open the operating system's own folder dialog on this machine and return the chosen path.

        Runs in a separate process (GUI toolkits must own their main thread). The server is local-only, so the
        dialog appears on the same screen as the browser. Returns 501 when tkinter is unavailable (some Linux
        installs), and the UI then falls back to its in-app folder browser.
        """
        initial = body.initial if body.initial and Path(body.initial).is_dir() else ""
        try:
            done = subprocess.run(
                [sys.executable, "-c", _PICK_SCRIPT, initial, body.title[:80]],
                capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(408, "folder dialog timed out")
        if done.returncode == 3:
            raise HTTPException(501, "native folder dialog is not available on this system")
        if done.returncode != 0:
            raise HTTPException(500, "folder dialog failed")
        chosen = done.stdout.strip()
        return {"path": chosen or None, "cancelled": not chosen}

    # -- jobs ---------------------------------------------------------------------------------------
    def run_scan(job_id: str, input_dir: Path, provider_name: str) -> None:
        store, journal = stores()
        try:
            files = discover_audio(input_dir)
            progress[job_id].update(total=len(files), state="scanning")
            provider = provider_factory(provider_name)

            def tick(done: int, total: int, label: str) -> None:
                progress[job_id].update(done=done, total=total, label=label)

            rows = match_library(files, provider, progress=tick, root=input_dir)
            store.save_rows(job_id, rows)
            store.set_state(job_id, "scanned")
            progress[job_id].update(state="scanned", done=len(files), label="finished")
        except Exception as exc:  # report, do not crash the server thread
            store.set_state(job_id, "failed")
            progress[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            store.close()
            journal.close()

    @app.post("/api/jobs", status_code=201)
    def create_job(body: NewJob):
        inp, out = Path(body.input_dir), Path(body.output_dir)
        if not inp.is_dir():
            raise HTTPException(400, f"input folder not found: {inp}")
        if not out.parent.exists():
            raise HTTPException(400, f"output folder's parent does not exist: {out.parent}")
        if inp.resolve() == out.resolve():
            raise HTTPException(400, "input and output folders must differ")
        from .cli import parse_providers

        try:
            parse_providers(body.provider)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        count = len(discover_audio(inp))
        if not count:
            raise HTTPException(400, "no supported audio files in the input folder")
        store, journal = stores()
        try:
            job_id = journal.new_job_id()
            store.create_job(job_id, inp.resolve(), out.resolve())
        finally:
            store.close()
            journal.close()
        progress[job_id] = {"state": "scanning", "done": 0, "total": count, "label": "starting", "error": ""}
        threading.Thread(target=run_scan, args=(job_id, inp.resolve(), body.provider), daemon=True).start()
        return {"id": job_id, "files": count}

    @app.get("/api/jobs")
    def list_jobs():
        store, journal = stores()
        try:
            return [{**j, "counts": store.counts(j["id"])} for j in store.list_jobs()]
        finally:
            store.close()
            journal.close()

    def snapshot(job_id: str) -> dict:
        store, journal = stores()
        try:
            job = job_or_404(store, job_id)
            live = progress.get(job_id, {})
            counts = store.counts(job_id)
            kinds = {o["op_type"] for o in journal.operations(job_id)}
            applied_mode = "copy" if "copy" in kinds else ("move" if "move" in kinds else "")
            return {**job, "counts": counts, "applied_mode": applied_mode, "pending_review": len(store.pending_review(job_id)),
                    "progress": {"done": live.get("done", sum(counts.values())),
                                 "total": live.get("total", sum(counts.values())),
                                 "label": live.get("label", ""), "error": live.get("error", "")}}
        finally:
            store.close()
            journal.close()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return snapshot(job_id)

    @app.websocket("/api/jobs/{job_id}/ws")
    async def job_ws(ws: WebSocket, job_id: str):
        if not _is_local(ws.headers.get("host")) or not _is_local(ws.headers.get("origin")):
            await ws.close(code=1008)
            return
        await ws.accept()
        import asyncio

        try:
            last = None
            while True:
                snap = await asyncio.to_thread(snapshot, job_id)
                key = json.dumps(snap, sort_keys=True, default=str)
                if key != last:
                    await ws.send_json(snap)
                    last = key
                if snap["state"] in {"scanned", "applied", "failed"} and snap["progress"]["done"] >= snap["progress"]["total"]:
                    break
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, HTTPException):
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    @app.get("/api/jobs/{job_id}/tracks")
    def tracks(job_id: str, status: str | None = None, offset: int = 0, limit: int = Query(200, le=1000)):
        store, journal = stores()
        try:
            job_or_404(store, job_id)
            rows = store.load_rows(job_id, (status,) if status else None)
            slim = [{k: v for k, v in r.items() if k != "candidates"} for r in rows]
            return {"total": len(slim), "items": slim[offset:offset + limit]}
        finally:
            store.close()
            journal.close()

    # -- review -------------------------------------------------------------------------------------
    @app.get("/api/jobs/{job_id}/review")
    def review_queue(job_id: str):
        store, journal = stores()
        try:
            job_or_404(store, job_id)
            items = []
            for row in store.pending_review(job_id):
                src = Path(row["source"])
                try:
                    local = read_local(src) if src.exists() else {}
                except Exception:
                    local = {}
                items.append({"source": row["source"], "status": row["status"], "title": row.get("local_title"),
                              "reason": row.get("detail_status"), "duplicate_of": row.get("duplicate_of", ""),
                              "local": {f: local.get(f, "") for f in FIELDS}, "candidates": _candidates(row)})
            return {"defaults": DEFAULT_CHOICES, "fields": list(FIELDS), "items": items}
        finally:
            store.close()
            journal.close()

    def _selected(store: Store, job_id: str, body: ReviewBody):
        job = job_or_404(store, job_id)
        row = next((r for r in store.load_rows(job_id) if r["source"] == body.source), None)
        if not row:
            raise HTTPException(404, "unknown track")
        cands = _candidates(row)
        if body.candidate_index >= len(cands):
            raise HTTPException(400, "candidate_index out of range")
        return job, row, cands[body.candidate_index]

    def _validate_choices(choices: dict) -> None:
        for field, c in choices.items():
            if field not in FIELDS or c.get("mode") not in {"local", "provider", "manual", "keep"}:
                raise HTTPException(400, f"bad choice for {field!r}")

    @app.post("/api/jobs/{job_id}/review/preview")
    def review_preview(job_id: str, body: ReviewBody):
        _validate_choices(body.choices)
        store, journal = stores()
        try:
            job, row, cand = _selected(store, job_id, body)
            src = Path(row["source"])
            if not src.exists():
                raise HTTPException(409, "source file no longer exists")
            local = read_local(src)
            values = resolve_values(local, provider_values(cand, row), body.choices)
            folder, stem = manual_destination(local, values, Path(job["output_dir"]))
            return {"values": values, "local": {f: local.get(f, "") for f in FIELDS},
                    "provider": provider_values(cand, row), "destination": str(folder / (stem + src.suffix.lower()))}
        finally:
            store.close()
            journal.close()

    @app.post("/api/jobs/{job_id}/review/decision")
    def review_decision(job_id: str, body: ReviewBody):
        _validate_choices(body.choices)
        store, journal = stores()
        try:
            _, row, cand = _selected(store, job_id, body)
            store.save_decision(job_id, row["source"], cand, body.choices)
            return {"saved": True, "pending": len(store.pending_review(job_id))}
        finally:
            store.close()
            journal.close()

    @app.post("/api/jobs/{job_id}/duplicates/keep-both")
    def keep_both(job_id: str, body: SourceBody):
        store, journal = stores()
        try:
            job_or_404(store, job_id)
            row = next((r for r in store.load_rows(job_id) if r["source"] == body.source), None)
            if not row or row.get("status") != "REVIEW_DUPLICATE":
                raise HTTPException(400, "not a duplicate row")
            row["status"] = "AUTO_MATCH"
            row["detail_status"] += " | user chose to keep both copies"
            store.save_rows(job_id, [row])
            return {"saved": True}
        finally:
            store.close()
            journal.close()

    # -- plan / apply / undo ------------------------------------------------------------------------
    @app.get("/api/jobs/{job_id}/plan")
    def plan(job_id: str):
        store, journal = stores()
        try:
            job = job_or_404(store, job_id)
            p = plan_moves(store.load_rows(job_id), Path(job["output_dir"]))
            return {"will_move": len(p.movable), "stay": len(p.untouched),
                    "stay_by_status": dict(Counter(r.get("status") for r in p.untouched)),
                    "ops": [{"source": str(o.source), "dest": str(o.dest), "collision": o.collision,
                             "problem": o.problem} for o in p.ops]}
        finally:
            store.close()
            journal.close()

    def _check_mode(mode: str) -> None:
        if mode not in ("move", "copy"):
            raise HTTPException(400, "mode must be 'move' or 'copy'")

    @app.post("/api/jobs/{job_id}/apply")
    def apply(job_id: str, body: ApplyBody):
        _check_mode(body.mode)
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to move or copy files")
        with lock_for(job_id):
            store, journal = stores()
            try:
                job = job_or_404(store, job_id)
                rows = store.load_rows(job_id)
                p = plan_moves(rows, Path(job["output_dir"]))
                apply_plan(p, journal, job_id=job_id, mode=body.mode)
                store.save_rows(job_id, rows)
                store.set_state(job_id, "applied")
                done = Counter(o["status"] for o in journal.operations(job_id) if o["op_type"] == body.mode)
                return {"results": dict(done)}
            finally:
                store.close()
                journal.close()

    @app.post("/api/jobs/{job_id}/apply-reviewed")
    def apply_reviewed(job_id: str, body: ApplyBody):
        _check_mode(body.mode)
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to move or copy files")
        with lock_for(job_id):
            store, journal = stores()
            try:
                job = job_or_404(store, job_id)
                moved = failed = 0
                for row in store.load_rows(job_id):
                    d = store.get_decision(job_id, row["source"])
                    if not d or d["applied"]:
                        continue
                    p = plan_manual(row, d["candidate"], Path(job["output_dir"]), d["choices"])
                    apply_plan(p, journal, job_id=job_id, mode=body.mode)
                    if row.get("status") in ("MOVED", "COPIED"):
                        row["manual_resolution"] = "applied"
                        store.mark_applied(job_id, row["source"])
                        moved += 1
                    else:
                        failed += 1
                    store.save_rows(job_id, [row])
                return {"moved": moved, "failed": failed}
            finally:
                store.close()
                journal.close()

    @app.post("/api/jobs/{job_id}/undo")
    def undo(job_id: str, body: Confirm):
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to restore files")
        with lock_for(job_id):
            store, journal = stores()
            try:
                job = job_or_404(store, job_id)
                result = undo_job(journal, job_id, Path(job["output_dir"]))
                return {"undone": result["undone"], "skipped": [{"path": p, "reason": r} for p, r in result["skipped"]]}
            finally:
                store.close()
                journal.close()

    @app.get("/api/jobs/{job_id}/cleanup")
    def cleanup_preview(job_id: str):
        """Folders in the INPUT folder that are now empty or hold only leftovers. Preview only."""
        store, journal = stores()
        try:
            job = job_or_404(store, job_id)
            copied = any(o["op_type"] == "copy" for o in journal.operations(job_id))
            dirs = [] if copied else plan_cleanup(Path(job["input_dir"]))   # a copy removes nothing from the input
            return {"trash_available": trash_fn is not None, "copied": copied, "dirs": [
                {"path": str(d.path), "kind": d.kind, "files": d.files, "has_images": d.has_images,
                 "empty_subfolders": d.empty_subfolders, "default_selected": d.default_selected}
                for d in dirs]}
        finally:
            store.close()
            journal.close()

    @app.post("/api/jobs/{job_id}/cleanup")
    def cleanup_apply(job_id: str, body: CleanupBody):
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to remove folders")
        with lock_for(job_id):
            store, journal = stores()
            try:
                job = job_or_404(store, job_id)
                if any(o["op_type"] == "copy" for o in journal.operations(job_id)):
                    raise HTTPException(409, "this job copied files; nothing was removed from the input folder")
                return apply_cleanup(Path(job["input_dir"]), body.paths, journal, job_id, trash_fn)
            finally:
                store.close()
                journal.close()

    @app.get("/api/jobs/{job_id}/operations")
    def operations(job_id: str):
        store, journal = stores()
        try:
            job_or_404(store, job_id)
            return [{k: o[k] for k in ("id", "op_type", "source", "dest", "status", "error")}
                    for o in journal.operations(job_id)]
        finally:
            store.close()
            journal.close()

    @app.get("/api/jobs/{job_id}/report.csv", response_class=PlainTextResponse)
    def report(job_id: str):
        store, journal = stores()
        try:
            job_or_404(store, job_id)
            rows = [{k: v for k, v in r.items() if k != "candidates"} for r in store.load_rows(job_id)]
            cols = sorted({k for r in rows for k in r})
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
            return PlainTextResponse(buf.getvalue(), media_type="text/csv")
        finally:
            store.close()
            journal.close()

    # -- repair existing library (tags fixed in place; preview first) ----------------------------------
    def run_repair_preview(rid: str, folder: Path, use_itunes: bool, artist_fallback: bool) -> None:
        r = repairs[rid]
        try:
            states, skipped = scan_folder(folder)
            by_path = {s.path: s for s in states}
            r.update(total=len(states), skipped=[str(p) for p in skipped], state="planning")
            lookup = None
            if use_itunes:
                lookup = (TrackGenreLookup(Path(db_path).with_name("itunes_genre_cache.json"))
                          if genre_lookup is _DEFAULT else genre_lookup)

            def counted(artist: str, title: str):
                r["done"] += 1
                return lookup(artist, title)

            changes = plan_repair(states, itunes_artist_genre if (artist_fallback and use_itunes and genre_lookup is _DEFAULT) else None,
                                  counted if lookup else None, use_library_majority=artist_fallback)
            r.update(changes=changes, states=by_path, done=len(states), state="ready")
        except Exception as exc:
            r.update(state="failed", error=f"{type(exc).__name__}: {exc}")

    def repair_row(folder: Path, c, states: dict) -> dict:
        s = states.get(c.path)
        junk = [t for k, texts in (s.comments if s else []) if k in c.remove_comment_keys for t in texts]
        try:
            rel = str(c.path.relative_to(folder))
        except ValueError:
            rel = str(c.path)
        return {"path": rel, "title": s.title if s else "", "removed_comments": junk,
                "old_genre": s.genres if s else [], "new_genre": c.new_genre or [], "clear_genre": c.clear_genre,
                "genre_source": c.genre_source, "overrides": bool(c.replaced), "fills": c.fills, "notes": c.fill_notes}

    def repair_summary(r: dict) -> dict:
        ch = r.get("changes", [])
        return {"files": r.get("total", 0), "will_change": len(ch),
                "junk_comments": sum(bool(c.remove_comment_keys) for c in ch),
                "junk_genres_cleared": sum(c.clear_genre for c in ch),
                "genres_filled": sum(bool(c.new_genre and not c.replaced) for c in ch),
                "genres_overridden": sum(bool(c.replaced) for c in ch),
                "album_artist_filled": sum("TPE2" in c.fills for c in ch),
                "track_number_filled": sum("TRCK" in c.fills for c in ch),
                "year_filled": sum("TDRC" in c.fills for c in ch),
                "left_alone": sum(bool(c.fill_notes) for c in ch)}

    def repair_or_404(rid: str) -> dict:
        r = repairs.get(rid)
        if not r:
            raise HTTPException(404, "unknown repair (previews are kept in memory and are lost when the server restarts)")
        return r

    @app.post("/api/repair", status_code=201)
    def repair_start(body: RepairBody):
        folder = Path(body.folder)
        if not folder.is_dir():
            raise HTTPException(400, f"folder not found: {folder}")
        rid = Journal.new_job_id()
        repairs[rid] = {"state": "scanning", "folder": str(folder.resolve()), "done": 0, "total": 0, "changes": [],
                        "states": {}, "skipped": [], "error": "", "job_id": ""}
        threading.Thread(target=run_repair_preview, args=(rid, folder.resolve(), body.itunes, body.artist_fallback),
                         daemon=True).start()
        return {"id": rid}

    @app.get("/api/repair/{rid}")
    def repair_status(rid: str):
        r = repair_or_404(rid)
        return {"id": rid, "state": r["state"], "folder": r["folder"], "error": r["error"], "job_id": r["job_id"],
                "progress": {"done": r["done"], "total": r["total"]}, "skipped": len(r["skipped"]),
                "summary": repair_summary(r)}

    @app.get("/api/repair/{rid}/changes")
    def repair_changes(rid: str, offset: int = 0, limit: int = Query(300, le=2000)):
        r = repair_or_404(rid)
        folder = Path(r["folder"])
        items = [repair_row(folder, c, r["states"]) for c in r["changes"][offset:offset + limit]]
        return {"total": len(r["changes"]), "items": items}

    @app.post("/api/repair/{rid}/apply")
    def repair_apply(rid: str, body: Confirm):
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to change tags")
        r = repair_or_404(rid)
        with lock_for(rid):
            if r["state"] != "ready":
                raise HTTPException(409, f"repair is {r['state']}, not ready")
            store, journal = stores()
            try:
                job = apply_changes(r["changes"], journal)
                r.update(state="applied", job_id=job)
                done = Counter(o["status"] for o in journal.operations(job))
                return {"job_id": job, "results": dict(done)}
            finally:
                store.close()
                journal.close()

    @app.post("/api/repair/{rid}/undo")
    def repair_undo(rid: str, body: Confirm):
        if not body.confirm:
            raise HTTPException(400, "send {\"confirm\": true} to restore tags")
        r = repair_or_404(rid)
        with lock_for(rid):
            if not r["job_id"]:
                raise HTTPException(409, "nothing to undo")
            store, journal = stores()
            try:
                res = undo_job(journal, r["job_id"])
                r["state"] = "ready"
                return {"undone": res["undone"], "skipped": [{"path": p, "reason": why} for p, why in res["skipped"]]}
            finally:
                store.close()
                journal.close()

    return app


def serve(db_path: Path | str, port: int = 8765, open_browser: bool = True) -> None:
    """Run the app on loopback only and open the browser."""
    import uvicorn

    app = create_app(db_path)
    static = Path(__file__).parent / "static"
    if static.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
