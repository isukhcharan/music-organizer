"""Terminal interface. Thin layer over the core: scan -> plan -> apply -> undo.

    python -m musicorganizer scan  --input IN --output OUT
    python -m musicorganizer plan  --job JOB
    python -m musicorganizer apply --job JOB --yes
    python -m musicorganizer undo  --job JOB
    python -m musicorganizer report --job JOB --csv report.csv
    python -m musicorganizer jobs

State lives in one SQLite file (default: ~/.musicorganizer/state.db). Nothing is moved by ``scan`` or ``plan``.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from .journal import Journal
from .organizer import apply_plan, plan_moves, undo_job
from .pipeline import discover_audio, match_library
from .resolver import (
    FIELDS,
    manual_destination,
    parse_overrides,
    plan_manual,
    provider_values,
    resolve_values,
)
from .store import Store
from .tags import read_local

DEFAULT_DB = Path.home() / ".musicorganizer" / "state.db"


def build_provider(name: str = "auto"):
    """``auto`` = YouTube Music first with JioSaavn as the fallback (JioSaavn skipped if musicapy is missing)."""
    from .providers import ChainProvider, SaavnProvider, YTMusicProvider

    if name == "ytmusic":
        return ChainProvider([YTMusicProvider()])
    if name == "jiosaavn":
        return ChainProvider([SaavnProvider()])
    providers = [YTMusicProvider()]
    try:
        providers.append(SaavnProvider())
    except Exception:  # musicapy not installed: YouTube Music only
        pass
    return ChainProvider(providers)


def cmd_scan(args, store: Store, journal: Journal) -> int:
    inp, out = Path(args.input).resolve(), Path(args.output).resolve()
    if not inp.is_dir():
        print(f"Input folder not found: {inp}")
        return 2
    files = discover_audio(inp)
    if not files:
        print("No supported audio files found.")
        return 2
    job_id = journal.new_job_id()
    store.create_job(job_id, inp, out)
    print(f"Job {job_id}: {len(files)} files in {inp}")
    provider = build_provider(args.provider)
    rows = match_library(files, provider, progress=lambda d, t, label: print(f"[{d}/{t}] {label}", flush=True),
                         root=inp)
    store.save_rows(job_id, rows)
    store.set_state(job_id, "scanned")
    print("\nResults:")
    for status, n in sorted(Counter(r.get("status") for r in rows).items()):
        print(f"  {status}: {n}")
    print(f"\nNothing was moved. Next:  python -m musicorganizer plan --job {job_id}")
    return 0


def _job(store: Store, job_id: str):
    job = store.get_job(job_id)
    if not job:
        print(f"Unknown job: {job_id}")
    return job


def cmd_plan(args, store: Store, journal: Journal) -> int:
    job = _job(store, args.job)
    if not job:
        return 2
    rows = store.load_rows(args.job)
    plan = plan_moves(rows, Path(job["output_dir"]))
    for op in plan.ops:
        if op.problem:
            print(f"SKIP  {op.source.name}: {op.problem}")
            continue
        flag = "  [name taken -> renamed]" if op.collision else ""
        print(f"MOVE  {op.source}\n   -> {op.dest}{flag}")
    print(f"\n{len(plan.movable)} will move, {len(plan.untouched)} stay in place, "
          f"{sum(1 for o in plan.ops if o.problem)} skipped.")
    if plan.untouched:
        print("Staying put:", dict(Counter(r.get("status") for r in plan.untouched)))
    return 0


def cmd_apply(args, store: Store, journal: Journal) -> int:
    job = _job(store, args.job)
    if not job:
        return 2
    if not args.yes:
        print("Refusing to move files without --yes. Run `plan` first, then add --yes.")
        return 2
    rows = store.load_rows(args.job)
    plan = plan_moves(rows, Path(job["output_dir"]))
    apply_plan(plan, journal, job_id=args.job)
    store.save_rows(args.job, rows)
    store.set_state(args.job, "applied")
    done = Counter(o["status"] for o in journal.operations(args.job) if o["op_type"] == "move")
    print(f"Done: {dict(done)}. Undo with: python -m musicorganizer undo --job {args.job}")
    return 0 if not done.get("failed") else 1


def _show_candidates(row: dict) -> list[dict]:
    raw = row.get("candidates") or "[]"
    try:
        candidates = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        candidates = []
    for i, c in enumerate(candidates[:8], 1):
        print(f"  {i}. {c.get('title', '?')} - {c.get('artist', '?')} | album: {c.get('album_hint') or '?'} "
              f"| score {c.get('score', '?')}")
    return candidates[:8]


def cmd_review(args, store: Store, journal: Journal, ask=input) -> int:
    """Walk the unresolved tracks. Decisions are saved immediately; nothing is moved here."""
    job = _job(store, args.job)
    if not job:
        return 2
    pending = store.pending_review(args.job)
    if not pending:
        print("Nothing left to review.")
        return 0
    out_root = Path(job["output_dir"])
    for i, row in enumerate(pending, 1):
        print("\n" + "=" * 72)
        print(f"[{i}/{len(pending)}] {row['status']}: {row.get('local_title')}")
        print(f"  file:   {row['source']}")
        print(f"  local:  artist={row.get('local_artist') or '(blank)'} | "
              f"album={row.get('local_album') or row.get('folder_album_hint') or '(blank)'}")
        print(f"  reason: {row.get('detail_status') or '-'}")
        if row["status"] == "REVIEW_DUPLICATE":
            print(f"  other copy: {row.get('duplicate_of')}")
            choice = ask("  [b] keep both (second gets a ' (2)' name)  [s] skip  [q] quit: ").strip().lower()
            if choice == "q":
                break
            if choice == "b":
                row["status"] = "AUTO_MATCH"
                row["detail_status"] += " | user chose to keep both copies"
                store.save_rows(args.job, [row])
                print("  -> will be moved with the other approved tracks (run `apply`).")
            continue
        candidates = _show_candidates(row)
        if not candidates:
            print("  (no provider candidates; skipping)")
            continue
        pick = ask("  Candidate number, [s]kip, [q]uit: ").strip().lower()
        if pick == "q":
            break
        if not pick.isdigit() or not 1 <= int(pick) <= len(candidates):
            continue
        cand = candidates[int(pick) - 1]
        local = read_local(Path(row["source"]))
        choices: dict = {}
        while True:
            values = resolve_values(local, provider_values(cand, row), choices)
            folder, stem = manual_destination(local, values, out_root)
            print("  Will write (- = leave as is):")
            for f in FIELDS:
                print(f"    {f:<12} {values[f] or '-':<40} (local: {local.get(f) or '-'})")
            print(f"  Destination: {folder / (stem + Path(row['source']).suffix.lower())}")
            text = ask("  Enter = accept | overrides like  title=l genre=m:Punjabi | [s]kip: ").strip()
            if text.lower() == "s":
                break
            if not text:
                store.save_decision(args.job, row["source"], cand, choices)
                print("  saved.")
                break
            try:
                choices.update(parse_overrides(text))
            except ValueError as exc:
                print(f"  {exc}")
    left = len(store.pending_review(args.job))
    print(f"\n{left} still pending. Apply saved decisions with:  "
          f"python -m musicorganizer apply-reviewed --job {args.job} --yes")
    return 0


def cmd_apply_reviewed(args, store: Store, journal: Journal) -> int:
    job = _job(store, args.job)
    if not job:
        return 2
    if not args.yes:
        print("Refusing to move files without --yes.")
        return 2
    out_root, moved, failed = Path(job["output_dir"]), 0, 0
    for row in store.load_rows(args.job):
        decision = store.get_decision(args.job, row["source"])
        if not decision or decision["applied"]:
            continue
        plan = plan_manual(row, decision["candidate"], out_root, decision["choices"])
        apply_plan(plan, journal, job_id=args.job)
        ok = row.get("status") == "MOVED"
        if ok:
            row["manual_resolution"] = "applied"
            store.mark_applied(args.job, row["source"])
            moved += 1
        else:
            failed += 1
        store.save_rows(args.job, [row])
        print(f"{'MOVED ' if ok else 'FAILED'} {Path(row['source']).name} -> {row.get('output', '')}")
    print(f"\nManually resolved: {moved} moved, {failed} failed.")
    return 0 if not failed else 1


def cmd_undo(args, store: Store, journal: Journal) -> int:
    job = store.get_job(args.job)
    result = undo_job(journal, args.job, Path(job["output_dir"]) if job else None)
    print(f"Restored {result['undone']} file(s).")
    for path, reason in result["skipped"]:
        print(f"  skipped {path}: {reason}")
    return 0


def cmd_report(args, store: Store, journal: Journal) -> int:
    rows = store.load_rows(args.job)
    cols = sorted({k for r in rows for k in r})
    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.csv}")
    return 0


def cmd_jobs(args, store: Store, journal: Journal) -> int:
    for j in store.list_jobs():
        print(f"{j['id']}  {j['state']:<9} {j['input_dir']} -> {j['output_dir']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="musicorganizer", description="Local music library organizer")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite state file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="identify tracks (moves nothing)")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--provider", choices=["auto", "ytmusic", "jiosaavn"], default="auto",
                   help="auto = YouTube Music first, JioSaavn as fallback")
    p.set_defaults(fn=cmd_scan)
    for name, fn, help_ in (("plan", cmd_plan, "preview moves"), ("apply", cmd_apply, "move approved tracks"),
                            ("undo", cmd_undo, "restore moved files"), ("report", cmd_report, "export CSV")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--job", required=True)
        if name == "apply":
            p.add_argument("--yes", action="store_true", help="confirm that files should be moved")
        if name == "report":
            p.add_argument("--csv", required=True)
        p.set_defaults(fn=fn)
    p = sub.add_parser("review", help="resolve unmatched tracks (saves decisions, moves nothing)")
    p.add_argument("--job", required=True)
    p.set_defaults(fn=cmd_review)
    p = sub.add_parser("apply-reviewed", help="move tracks according to saved review decisions")
    p.add_argument("--job", required=True)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_apply_reviewed)
    sub.add_parser("jobs", help="list jobs").set_defaults(fn=cmd_jobs)
    args = parser.parse_args(argv)
    store, journal = Store(args.db), Journal(args.db)
    try:
        return args.fn(args, store, journal)
    finally:
        store.close()
        journal.close()


if __name__ == "__main__":
    sys.exit(main())
