"""Replay the real v15 report through the new layout code and compare against what the legacy
script actually produced. Skipped when the report is not on this machine (it is not in git).
"""
import json
import os
from pathlib import Path, PureWindowsPath

import pytest

from musicorganizer.layout import destination_dir, infer_album_artists

REPORT = Path(os.environ.get("MO_LEGACY_REPORT", r"C:\MusicOrganizer\data\organizer-v15-report.json"))
pytestmark = pytest.mark.skipif(not REPORT.exists(), reason="legacy v15 report not available")


def test_destination_folders_match_legacy_output():
    rows = json.loads(REPORT.read_text(encoding="utf-8"))
    infer_album_artists(rows)
    out_root = Path("OUT")
    checked, mismatches = 0, []
    for r in rows:
        if not r.get("output") or r.get("status") not in {"AUTO_MATCH", "MOVED"}:
            continue
        # Legacy output paths are Windows paths; compare the two folder components below Output.
        recorded = PureWindowsPath(r["output"]).parent.parts[-2:]
        got = destination_dir(
            r, out_root,
            local_album=r.get("local_album", ""),
            local_albumartist=r.get("local_albumartist", ""),
            local_artist=r.get("local_artist", ""),
        ).parts[-2:]
        checked += 1
        if tuple(recorded) != tuple(got):
            mismatches.append((r["source"], recorded, got))
    assert checked > 100
    assert not mismatches, f"{len(mismatches)}/{checked} differ, e.g. {mismatches[:3]}"
