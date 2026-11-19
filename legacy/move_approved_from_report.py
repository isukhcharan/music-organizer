#!/usr/bin/env python3
"""Move AUTO_MATCH files from an existing Music Organizer JSON report.

This reuses the saved decisions instead of searching providers again.
Preview is the default; pass --apply to move files.
"""
import argparse, csv, json
from pathlib import Path
import music_organizer_v15_move as organizer


def main():
    parser = argparse.ArgumentParser(description="Move approved files using an existing organizer report.")
    parser.add_argument("--report", default=str(organizer.DATA / "organizer-v14-report.json"), help="Existing organizer JSON report")
    parser.add_argument("--apply", action="store_true", help="Actually move AUTO_MATCH files")
    args = parser.parse_args()
    report_path = Path(args.report)
    if not report_path.exists(): raise SystemExit(f"Report not found: {report_path}")
    rows = json.loads(report_path.read_text(encoding="utf-8"))
    approved = [r for r in rows if r.get("status") == "AUTO_MATCH"]
    print(f"Found {len(approved)} AUTO_MATCH rows; {len(rows)-len(approved)} other rows will remain untouched.")
    print("Mode:", "APPLY — files will be moved" if args.apply else "PREVIEW — no files will move")
    if args.apply:
        organizer.OUT.mkdir(parents=True, exist_ok=True)
        for i, row in enumerate(approved, 1):
            try:
                organizer.move_approved(row)
                print(f"[{i}/{len(approved)}] MOVED: {row.get('source')} -> {row.get('output')}")
                row["status"] = "MOVED"
            except Exception as exc:
                row["status"] = "MOVE_ERROR"
                row["detail_status"] = f"{row.get('detail_status','')}; move failed: {type(exc).__name__}: {exc}"
                print(f"[{i}/{len(approved)}] ERROR: {row.get('source')}: {exc}")
    else:
        for row in approved:
            print(f"WOULD MOVE: {row.get('source')} -> Output (destination determined by v15 rules)")
    out_json = organizer.DATA / "organizer-v15-report.json"
    out_csv = organizer.DATA / "organizer-v15-report.csv"
    organizer.DATA.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    columns = sorted({k for r in rows for k in r})
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    print(f"Updated report: {out_json}")
    print(f"CSV report: {out_csv}")

if __name__ == "__main__": main()
