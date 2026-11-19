"""Scan -> match pipeline (the legacy main() loop, without file moves or CLI)."""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Callable

from .duplicates import DuplicateTracker
from .layout import infer_album_artists
from .matcher import (
    ALBUM_FIRST_FAILED_PREFIXES,
    identify_album_group,
    identify_single,
)
from .providers import Provider
from .tags import AUDIO_EXTS, read_local
from .text import norm, primary_artist

log = logging.getLogger(__name__)
Progress = Callable[[int, int, str], None]


def discover_audio(root: Path) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def _error_row(path: Path, method: str, exc: Exception, prefix: str = "") -> dict:
    return {"source": str(path), "local_title": path.stem, "status": "ERROR", "score": 0,
            "detail_status": f"{prefix}{type(exc).__name__}: {exc}", "match_method": method}


def flag_same_track_duplicates(rows: list[dict]) -> None:
    """Two files matched to the same track of the same release are probably the same song twice.

    Not byte-identical (so not caught earlier), but silently renaming one to "(2)" would hide it.
    The first file (by path) stays AUTO_MATCH; later ones go to review. Nothing is ever deleted.
    """
    seen: dict = {}
    for row in sorted((r for r in rows if r.get("status") == "AUTO_MATCH"), key=lambda r: r["source"]):
        key = (norm(row.get("matched_album")), row.get("track_number"), norm(row.get("matched_title")),
               norm(primary_artist(row.get("matched_artist") or row.get("local_artist"))))
        if key in seen:
            row["status"] = "REVIEW_DUPLICATE"
            row["duplicate_of"] = seen[key]
            row["detail_status"] = (f"Same release track as another file ({row.get('matched_album')}, "
                                    f"track {row.get('track_number')}); not byte-identical - keep one, or both")
        else:
            seen[key] = row["source"]


def match_library(files: list[Path], provider: Provider, progress: Progress | None = None,
                  root: Path | None = None) -> list[dict]:
    """Identify every file. Never moves or modifies files. Returns report rows (legacy shape).

    ``root`` is the input folder; files directly inside it get no folder-name album hint.
    """
    report: list[dict] = []
    duplicates = DuplicateTracker()
    groups: dict = defaultdict(list)
    ungrouped: list[Path] = []
    locals_cache: dict[Path, dict] = {}

    for path in files:
        try:
            locals_cache[path] = local = read_local(path, root)
        except Exception as exc:
            report.append(_error_row(path, "scan", exc, "Could not read tags: "))
            continue
        album = local.get("album") or local.get("folder_album_hint")
        if album:
            groups[(str(path.parent).lower(), norm(album))].append(path)
        else:
            ungrouped.append(path)

    total, done = len(files), len(report)

    def tick(label: str, n: int) -> None:
        nonlocal done
        done += n
        if progress:
            progress(done, total, label)

    for paths in groups.values():
        tick(f"Album-first: {paths[0].parent.name} ({len(paths)} files)", len(paths))
        try:
            rows = identify_album_group(paths, {p: locals_cache[p] for p in paths}, provider, duplicates)
        except Exception as exc:
            report.extend(_error_row(p, "album-first", exc, "Album-first failed: ") for p in paths)
            continue
        for row in rows:
            if row.get("status") == "REVIEW" and row.get("detail_status", "").startswith(ALBUM_FIRST_FAILED_PREFIXES):
                path = Path(row["source"])
                try:
                    # Legacy behaviour: song fallback runs with a fresh duplicate map.
                    fb = identify_single(path, locals_cache[path], provider, DuplicateTracker())
                    fb["match_method"] = "album-first+song-fallback"
                    if fb["status"] == "AUTO_MATCH":
                        fb["detail_status"] = "Individual song fallback: " + fb["detail_status"]
                    else:
                        fb["detail_status"] = f"Album-first: {row['detail_status']}; song fallback: {fb['detail_status']}"
                    row = fb
                except Exception as exc:
                    row["detail_status"] += f"; song fallback failed: {type(exc).__name__}: {exc}"
                    row["match_method"] = "album-first+song-fallback"
            report.append(row)

    for path in ungrouped:
        tick(f"Single/fallback: {path.name}", 1)
        try:
            row = identify_single(path, locals_cache[path], provider, duplicates)
            row["match_method"] = "song-fallback"
            report.append(row)
        except Exception as exc:
            report.append(_error_row(path, "song-fallback", exc))

    flag_same_track_duplicates(report)
    infer_album_artists(report)
    return report
