"""Safe organising: plan (preview) -> apply -> undo.

Safety rules (from the project brief):
  * ``plan_moves`` touches nothing. Destinations, collisions and untouched files are all visible up front.
  * Only AUTO_MATCH rows are ever moved. Review/conflict/duplicate/no-result files are left alone.
  * Nothing is overwritten. A taken destination gets a " (n)" suffix and the plan flags the collision.
  * The source is not modified. The file is copied to a temp name in the destination folder, provider values
    are written into *blank* tags of that copy, the copy is verified and renamed into place, and only then
    is the source removed. Any failure leaves the source where it was.
  * Every step is journaled so a job can be undone.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import File as MutagenFile

from .journal import Journal
from .repair import undo_retag
from .layout import destination_dir, destination_filename, infer_album_artists
from .tags import AUDIO_EXTS, fill_blank_tags, read_local, restore_tags, snapshot_tags, write_tags
from .text import primary_artist


@dataclass
class PlannedOp:
    row: dict
    source: Path
    dest: Path
    tag_values: dict
    overwrite: bool = False       # True only for explicit manual decisions
    collision: bool = False       # preferred name was taken, a suffix was added
    problem: str = ""             # non-empty => will be skipped
    sidecars: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass
class Plan:
    ops: list[PlannedOp]
    untouched: list[dict]         # rows that stay where they are

    @property
    def movable(self) -> list[PlannedOp]:
        return [o for o in self.ops if not o.problem]


def _unique_dest(directory: Path, name: str, reserved: set[Path]) -> tuple[Path, bool]:
    dest = directory / name
    if not dest.exists() and dest not in reserved:
        return dest, False
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while True:
        candidate = directory / f"{stem} ({n}){suffix}"
        if not candidate.exists() and candidate not in reserved:
            return candidate, True
        n += 1


def _sidecars(source: Path) -> list[Path]:
    """Non-audio files sharing the exact stem (e.g. .lrc). Exact compare: no glob metacharacter surprises."""
    if not source.parent.is_dir():
        return []
    return [p for p in source.parent.iterdir()
            if p.is_file() and p != source and p.stem == source.stem and p.suffix.lower() not in AUDIO_EXTS]


def plan_moves(rows: list[dict], out_root: Path) -> Plan:
    out_root = Path(out_root)
    # Rows can be promoted to AUTO_MATCH after the scan (e.g. "keep both" on a duplicate), so the
    # folder-level album artist is always (re)computed from the rows that are about to move.
    infer_album_artists(rows)
    ops: list[PlannedOp] = []
    untouched = [r for r in rows if r.get("status") != "AUTO_MATCH"]
    reserved: set[Path] = set()
    for row in (r for r in rows if r.get("status") == "AUTO_MATCH"):
        source = Path(row["source"])
        if not source.exists():
            ops.append(PlannedOp(row, source, source, {}, problem="source file not found"))
            continue
        try:
            local = read_local(source)
            raw_track = str(row.get("track_number") or "").strip()
            track = int(raw_track) if raw_track else None   # None: no trustworthy number, no filename prefix
        except Exception as exc:
            ops.append(PlannedOp(row, source, source, {}, problem=f"cannot plan: {type(exc).__name__}: {exc}"))
            continue
        folder = destination_dir(row, out_root, local_album=local["album"],
                                 local_albumartist=local["albumartist"], local_artist=local["artist"])
        name = destination_filename(track, local["title"], source.suffix, source.stem)
        dest, collided = _unique_dest(folder, name, reserved)
        reserved.add(dest)
        album_name = row.get("matched_album") or local["album"] or "Singles"
        values = {  # provider fills blanks only; populated local tags always win
            "title": row.get("matched_title") or local["title"],
            "artist": row.get("matched_artist") or local["artist"],
            "albumartist": local["albumartist"] or row.get("resolved_albumartist")
                           or row.get("matched_albumartist")
                           or primary_artist(local["artist"] or row.get("matched_artist")),
            "album": local["album"] or row.get("matched_album") or album_name,
            "date": local["date"] or row.get("matched_year"),
            "tracknumber": local["tracknumber"] or (str(track) if track else ""),
            "genre": local["genre"],
        }
        op = PlannedOp(row, source, dest, values, collision=collided)
        for sc in _sidecars(source):
            target, _ = _unique_dest(folder, sc.name, reserved)
            reserved.add(target)
            op.sidecars.append((sc, target))
        ops.append(op)
    return Plan(ops, untouched)


def _length(path: Path) -> float:
    audio = MutagenFile(path, easy=True)
    if audio is None or audio.info is None:
        raise OSError(f"not readable as audio: {path.name}")
    return float(audio.info.length)


def _verify(copy: Path, expected_length: float) -> None:
    """The tagged copy must still decode and have the same duration as the source."""
    if abs(_length(copy) - expected_length) > 0.01:
        raise OSError("audio duration changed while writing tags")


def apply_plan(plan: Plan, journal: Journal, job_id: str | None = None) -> str:
    """Execute the plan. Returns the job id. Never raises for a single file's failure."""
    job_id = job_id or journal.new_job_id()
    for op in plan.movable:
        op_id = journal.begin(job_id, "move", op.source, op.dest)
        tmp = op.dest.with_name(f".mo-tmp-{op.dest.name}")
        try:
            if not op.source.exists():
                raise FileNotFoundError("source disappeared before apply")
            if op.dest.exists():
                raise FileExistsError(f"destination appeared since preview: {op.dest}")
            op.dest.parent.mkdir(parents=True, exist_ok=True)
            expected_length = _length(op.source)
            before = snapshot_tags(op.source, op.tag_values)   # source is only read, never written
            shutil.copy2(op.source, tmp)
            writer = write_tags if op.overwrite else fill_blank_tags
            written = writer(tmp, op.tag_values)
            _verify(tmp, expected_length)
            os.replace(tmp, op.dest)
            op.source.unlink()
        except Exception as exc:
            if tmp.exists():
                tmp.unlink()
            journal.finish(op_id, "failed", f"{type(exc).__name__}: {exc}")
            op.row["status"] = "APPLY_ERROR"
            op.row["detail_status"] = f"{op.row.get('detail_status', '')}; move failed: {type(exc).__name__}: {exc}"
            continue
        journal.finish(op_id, "done", fields_written=written,
                       tags_before={f: before.get(f, []) for f in written})
        op.row["output"] = str(op.dest)
        op.row["status"] = "MOVED"
        for sc_src, sc_dest in op.sidecars:
            sid = journal.begin(job_id, "sidecar", sc_src, sc_dest)
            try:
                if sc_dest.exists():
                    raise FileExistsError(str(sc_dest))
                shutil.move(str(sc_src), str(sc_dest))
                journal.finish(sid, "done")
            except Exception as exc:
                journal.finish(sid, "failed", f"{type(exc).__name__}: {exc}")
    return job_id


def _prune_empty_dirs(start: Path, stop_at: Path | None) -> None:
    """Remove now-empty folders from ``start`` upward, never touching ``stop_at`` or anything above it."""
    current = start
    while True:
        if stop_at is not None and (current == stop_at or stop_at not in current.parents):
            return
        try:
            current.rmdir()  # only succeeds when empty
        except OSError:
            return
        if stop_at is None:
            return  # without a boundary, only the immediate parent is pruned
        current = current.parent


def undo_job(journal: Journal, job_id: str, stop_at: Path | None = None) -> dict:
    """Move files back to where they came from, newest first. Skips anything unsafe and says why.

    ``stop_at`` (the job's output folder) bounds the clean-up of emptied folders.
    """
    result = {"undone": 0, "skipped": []}
    for op in reversed(journal.operations(job_id)):
        if op["status"] != "done":
            continue
        if op["op_type"] == "retag":      # in-place tag repair: restore the removed/replaced frames
            try:
                undo_retag(Path(op["source"]), json.loads(op["tags_before"] or "{}"))
                journal.finish(op["id"], "undone")
                result["undone"] += 1
            except Exception as exc:
                journal.finish(op["id"], "undo_skipped", f"{type(exc).__name__}: {exc}")
                result["skipped"].append((op["source"], f"{type(exc).__name__}: {exc}"))
            continue
        src, dest = Path(op["source"]), Path(op["dest"])
        if not dest.exists():
            reason = "moved file no longer exists"
        elif src.exists():
            reason = "original location is occupied"
        else:
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
                _prune_empty_dirs(dest.parent, Path(stop_at) if stop_at else None)
                before = json.loads(op["tags_before"] or "{}")
                if before:
                    try:
                        restore_tags(src, before)
                    except Exception as exc:  # file is back; only its tags could not be reverted
                        result.setdefault("warnings", []).append((str(src), f"tags not restored: {exc}"))
                journal.finish(op["id"], "undone")
                result["undone"] += 1
                continue
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
        journal.finish(op["id"], "undo_skipped", reason)
        result["skipped"].append((str(dest), reason))
    return result
