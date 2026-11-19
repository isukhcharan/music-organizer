"""Folder-layout decisions: album-artist inference, single detection, destination paths.

Extracted from legacy/music_organizer_v15_move.py (infer_album_artists, is_standalone_single,
and the destination logic inside move_approved). Pure: a destination is computed, never created.

Rows are plain dicts shaped like the legacy report rows.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .text import clean_folder, norm, primary_artist

SOUNDTRACK_TOKENS = ("soundtrack", "original motion picture", " ost", "ost ")


def album_group_key(row: dict) -> tuple:
    """Rows with the same key share one inferred album artist (and so one folder).

    Tracks matched to the same multi-track release belong together even when they came from different
    source folders (e.g. a loose copy of a song that is also in the album folder). One-track releases
    are never merged across folders: unrelated singles sharing a title must not become a "compilation".
    """
    count = release_track_count(row)
    if row.get("matched_album") and count and count > 1:
        return ("release", norm(row["matched_album"]), count)
    album = row.get("local_album") or row.get("matched_album") or row.get("folder_album_hint") or "Singles"
    parent = Path(row.get("source", "")).parent.name
    return (norm(parent), norm(album))


def infer_album_artists(report: list[dict]) -> None:
    """Resolve folder-level album artists across all approved tracks (mutates rows in place)."""
    groups: dict = defaultdict(list)
    for row in report:
        if row.get("status") == "AUTO_MATCH":
            groups[album_group_key(row)].append(row)

    for rows in groups.values():
        provider = [r.get("matched_albumartist", "").strip() for r in rows if r.get("matched_albumartist", "").strip()]
        if provider:
            chosen = max(set(provider), key=provider.count)
            basis = "explicit provider albumartist"
        else:
            album_name = rows[0].get("matched_album") or rows[0].get("local_album") or ""
            artists: list[str] = []
            for r in rows:
                artist = primary_artist(r.get("matched_artist") or r.get("local_artist") or "")
                if artist and norm(artist) not in {norm(x) for x in artists}:
                    artists.append(artist)
            key_text = norm(album_name)
            is_soundtrack = any(x in key_text for x in SOUNDTRACK_TOKENS)
            if is_soundtrack or len(artists) > 1:
                chosen, basis = "Compilation", "multi-artist album grouping"
            elif artists:
                chosen, basis = artists[0], "single consistent track artist"
            else:
                chosen, basis = "Unknown Artist", "no reliable artist metadata"
        for r in rows:
            r["resolved_albumartist"] = chosen
            r["albumartist_basis"] = basis


_SINGLE_RE = re.compile(r"(?:Verified in\s+|album tracklist\s*\()(\d+)(?:-track release|\s+tracks)", re.I)


def release_track_count(row: dict) -> int | None:
    """Provider release size: the ``release_tracks`` field when present, else parsed from the detail text
    (both legacy formats, for reports produced by the old scripts)."""
    if isinstance(row.get("release_tracks"), int):
        return row["release_tracks"]
    match = _SINGLE_RE.search(str(row.get("detail_status") or ""))
    return int(match.group(1)) if match else None


def is_standalone_single(row: dict) -> bool:
    """A single only when the provider release has exactly one track.

    A lone local track from a multi-track album stays in its album folder.
    """
    return release_track_count(row) == 1


def destination_dir(
    row: dict,
    out_root: Path,
    local_album: str = "",
    local_albumartist: str = "",
    local_artist: str = "",
) -> Path:
    """Folder an approved track belongs in. Does not touch the filesystem."""
    album_name = row.get("matched_album") or local_album or "Singles"
    album_artist = (
        row.get("resolved_albumartist")
        or local_albumartist
        or primary_artist(local_artist)
        or "Unknown Artist"
    )
    album_key = norm(album_name + " " + row.get("matched_album", ""))
    if is_standalone_single(row):
        return out_root / clean_folder(album_artist, "Unknown Artist") / "Singles"
    if any(token in album_key for token in SOUNDTRACK_TOKENS):
        return out_root / "Soundtracks" / clean_folder(album_name, "Unknown Album")
    if row.get("albumartist_basis") == "multi-artist album grouping":
        return out_root / "Compilations" / clean_folder(album_name, "Unknown Album")
    return out_root / clean_folder(album_artist, "Unknown Artist") / clean_folder(album_name, "Singles")


def destination_filename(track_number: int | None, title: str, suffix: str, fallback_stem: str) -> str:
    """``NN - Title.ext``; no prefix when there is no trustworthy track number."""
    prefix = f"{track_number:02d} - " if track_number else ""
    return f"{prefix}{clean_folder(title, fallback_stem)}{suffix.lower()}"
