#!/usr/bin/env python3
"""Interactive resolver for Music Organizer v15 review rows.

Default is preview-only. With --apply, each confirmed decision moves the selected
source audio file into Output/Manual Review and applies only the metadata fields
the user explicitly chooses to change. Original populated tags are never silently
replaced. Duplicate rows are included but are never deleted automatically.
"""
import argparse, csv, json, re, shutil
from pathlib import Path
from mutagen import File as MutagenFile

BASE = Path(r"C:\MusicOrganizer")
OUT = BASE / "Output" / "Manual Review"
DATA = BASE / "data"
FIELDS = [
    ("title", "local_title", "title"),
    ("artist", "local_artist", "artist"),
    ("album", "local_album", "album_hint"),
    ("albumartist", "local_albumartist", None),
    ("date", "local_date", None),
    ("tracknumber", "local_tracknumber", None),
    ("genre", "local_genre", None),
]


def clean(value, fallback="Unknown"):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip().rstrip(".")
    return value[:120] or fallback


def choose_value(label, local, provider, default):
    """Choose a tag value; pressing Enter accepts the field-specific default."""
    local, provider = str(local or "").strip(), str(provider or "").strip()
    defaults = {"p": provider, "l": local, "blank": ""}
    default_value = defaults[default]
    default_label = {"p": "Provider", "l": "Local", "blank": "leave blank / unchanged"}[default]
    print(f"  {label}: [L]ocal={local or '(blank)'} | [P]rovider={provider or '(blank)'} | [M]anual | [K]eep current")
    print(f"  Default: {default_label}. Press Enter to accept.")
    while True:
        choice = input(f"  Choose L/P/M/K [Enter={default_label}]: ").strip().lower()
        if choice == "": return default_value
        if choice == "l": return local
        if choice == "p": return provider
        if choice == "m": return input(f"  Enter {label}: ").strip()
        if choice == "k": return None
        print("  Please enter L, P, M, K, or press Enter for the default.")


def read_easy(path):
    audio = MutagenFile(path, easy=True)
    if audio is None: return None
    if audio.tags is None: audio.add_tags()
    return audio


def local_tags(path):
    audio = MutagenFile(path, easy=True)
    tags = audio.tags if audio and audio.tags else {}
    def get(key):
        value = tags.get(key, [])
        if isinstance(value, list): return str(value[0]).strip() if value else ""
        return str(value or "").strip()
    return {
        "title": get("title") or path.stem,
        "artist": get("artist"),
        "album": get("album"),
        "albumartist": get("albumartist") or get("album artist"),
        "date": get("date") or get("year"),
        "tracknumber": get("tracknumber"),
        "genre": get("genre"),
    }


def move_resolved(row, provider, values, apply):
    source = Path(row["source"])
    if not source.exists():
        print(f"  Source not found (already moved?): {source}")
        return False
    artist = values.get("artist") or row.get("local_artist") or "Unknown Artist"
    album = values.get("album") or row.get("local_album") or row.get("folder_album_hint") or "Unsorted"
    title = values.get("title") or row.get("local_title") or source.stem
    track = values.get("tracknumber") or ""
    folder = OUT / clean(artist) / clean(album)
    prefix = f"{int(track):02d} - " if str(track).isdigit() else ""
    dest = folder / f"{prefix}{clean(title, source.stem)}{source.suffix.lower()}"
    n = 2
    while dest.exists():
        dest = folder / f"{prefix}{clean(title, source.stem)} ({n}){source.suffix.lower()}"
        n += 1
    print(f"  Destination: {dest}")
    if not apply:
        print("  PREVIEW only; nothing moved or edited.")
        return True
    folder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    # Apply only values explicitly selected; K means leave the original tag unchanged.
    try:
        audio = read_easy(dest)
        if audio is not None:
            for key, value in values.items():
                if value is not None and value != "":
                    audio[key] = [str(value)]
            audio.save()
    except Exception as exc:
        print(f"  WARNING: file moved, but tag writing failed: {type(exc).__name__}: {exc}")
    # Move adjacent non-audio sidecars when their destination does not already exist.
    audio_exts = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}
    for sidecar in source.parent.glob(source.stem + ".*"):
        if sidecar.suffix.lower() in audio_exts: continue
        target = folder / sidecar.name
        if not target.exists():
            try: shutil.move(str(sidecar), str(target))
            except OSError: pass
    row["manually_resolved_output"] = str(dest)
    row["manual_resolution"] = "applied"
    return True


def main():
    parser = argparse.ArgumentParser(description="Review unresolved music matches and choose local/provider/manual metadata.")
    parser.add_argument("--report", default=str(DATA / "organizer-v15-report.json"), help="Organizer JSON report")
    parser.add_argument("--apply", action="store_true", help="Actually move files and apply selected metadata; default is preview")
    args = parser.parse_args()
    report_path = Path(args.report)
    if not report_path.exists(): raise SystemExit(f"Report not found: {report_path}")
    rows = json.loads(report_path.read_text(encoding="utf-8"))
    statuses = {"REVIEW", "REVIEW_RELEASE_CONFLICT", "REVIEW_DUPLICATE", "NO_RESULT", "ERROR", "APPLY_ERROR"}
    pending = [r for r in rows if r.get("status") in statuses and not r.get("manual_resolution")]
    if not pending: print("No unresolved rows found."); return
    print(f"Found {len(pending)} unresolved rows. Mode: {'APPLY (files will move)' if args.apply else 'PREVIEW'}")
    for index, row in enumerate(pending, 1):
        print("\n" + "=" * 72)
        print(f"[{index}/{len(pending)}] {row.get('status')} — {row.get('local_title') or row.get('source')}")
        print(f"Local artist: {row.get('local_artist') or '(blank)'} | album: {row.get('local_album') or row.get('folder_album_hint') or '(blank)'}")
        print(f"Reason: {row.get('detail_status') or '(none)'}")
        print(f"File: {row.get('source')}")
        raw = row.get("candidates") or "[]"
        try: candidates = json.loads(raw) if isinstance(raw, str) else raw
        except Exception: candidates = []
        for i, c in enumerate(candidates[:8], 1):
            print(f"  {i}. [{c.get('source','?')}] {c.get('title','?')} — {c.get('artist','?')} | album: {c.get('album_hint') or '?'} | score: {c.get('score','?')}")
        if row.get("status") == "REVIEW_DUPLICATE":
            print("This is a byte-identical duplicate. It will not be deleted automatically.")
        action = input("Choose candidate number, or S=skip, Q=quit: ").strip().lower()
        if action == "q": break
        if action in {"", "s"}: continue
        if not action.isdigit() or not (1 <= int(action) <= len(candidates)):
            print("Invalid choice; skipping."); continue
        chosen = candidates[int(action)-1]
        values = {}
        try:
            actual_local = local_tags(Path(row["source"]))
        except Exception:
            actual_local = {}
        # Usual workflow: provider for title/artist/album; leave albumartist as-is;
        # local for year/track number; leave genre as-is. Enter accepts these defaults,
        # while L/P/M/K remain available to override any individual field.
        field_defaults = {
            "title": "p",
            "artist": "p",
            "album": "p",
            "albumartist": "blank",
            "date": "l",
            "tracknumber": "l",
            "genre": "blank",
        }
        for tag, local_key, provider_key in FIELDS:
            local = actual_local.get(tag) or row.get(local_key, "")
            provider = chosen.get(provider_key, "") if provider_key else ""
            # For date and track number, use report-level matched values when present.
            if tag == "date": provider = row.get("matched_year") or provider
            if tag == "tracknumber": provider = row.get("track_number") or provider
            values[tag] = choose_value(tag, local, provider, field_defaults[tag])
        print("\nSelected values (None means keep original tag):")
        for k, v in values.items(): print(f"  {k}: {'<KEEP>' if v is None else (v or '<blank>')}")
        confirm = input("Move this file using these choices? [y/N]: ").strip().lower()
        if confirm != "y": continue
        move_resolved(row, chosen, values, args.apply)
    # Persist decisions and the output locations to a separate resolution report.
    out_json = report_path.with_name("organizer-manual-resolution.json")
    out_csv = report_path.with_name("organizer-manual-resolution.csv")
    out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    cols = sorted({k for r in rows for k in r})
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(f"\nResolution report: {out_json}")
    print(f"CSV report: {out_csv}")
    if not args.apply: print("Preview only. Re-run with --apply to actually move and tag files.")

if __name__ == "__main__": main()
