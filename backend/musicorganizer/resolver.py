"""Manual conflict resolution: per-field choices -> tag values -> a move plan.

Replaces the L/P/M/K prompts of legacy/music_conflict_resolver.py with explicit data, so a UI
can drive it. The user always picks the provider candidate; nothing here auto-selects candidate 1.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from .layout import SOUNDTRACK_TOKENS
from .organizer import Plan, PlannedOp, _sidecars, _unique_dest
from .tags import read_local
from .text import clean_folder, norm, primary_artist

FIELDS = ("title", "artist", "album", "albumartist", "date", "tracknumber", "genre")
Mode = Literal["local", "provider", "manual", "keep"]

# The user's usual choices; any can be overridden per track.
DEFAULT_CHOICES: dict[str, Mode] = {
    "title": "provider", "artist": "provider", "album": "provider", "albumartist": "keep",
    "date": "local", "tracknumber": "local", "genre": "keep",
}


class Choice(TypedDict, total=False):
    mode: Mode
    value: str  # used when mode == "manual"


def provider_values(candidate: dict, row: dict) -> dict:
    """What the selected provider candidate offers for each field ('' when it has nothing)."""
    return {
        "title": candidate.get("title", ""), "artist": candidate.get("artist", ""),
        "album": candidate.get("album_hint", ""), "albumartist": "",
        "date": row.get("matched_year", "") or candidate.get("year", ""),
        "tracknumber": row.get("track_number", ""), "genre": "",
    }


def resolve_values(local: dict, provider: dict, choices: dict[str, Choice] | None = None) -> dict[str, str | None]:
    """Turn choices into tag values. ``None`` = leave the existing tag untouched.

    A blank value never erases anything: choosing "provider" when the provider is blank, or "local"
    when the local tag is blank, simply keeps the file's current tag.
    """
    merged: dict[str, Choice] = {f: {"mode": m} for f, m in DEFAULT_CHOICES.items()}
    for f, c in (choices or {}).items():
        if f in FIELDS:
            merged[f] = c
    out: dict[str, str | None] = {}
    for f in FIELDS:
        mode = merged[f].get("mode", "keep")
        if mode == "local":
            value = (local.get(f) or "").strip()
        elif mode == "provider":
            value = (provider.get(f) or "").strip()
        elif mode == "manual":
            value = (merged[f].get("value") or "").strip()
        else:
            value = ""
        out[f] = value or None
    return out


def manual_destination(local: dict, values: dict[str, str | None], out_root: Path) -> tuple[Path, str]:
    """Folder + filename for a manually resolved track, using the standard layout rules."""
    eff = {f: values.get(f) or local.get(f, "") for f in FIELDS}
    album = eff["album"]
    if album and any(t in norm(album) for t in SOUNDTRACK_TOKENS):
        folder = out_root / "Soundtracks" / clean_folder(album, "Unknown Album")
    elif album:
        artist = primary_artist(eff["albumartist"] or eff["artist"]) or "Unknown Artist"
        folder = out_root / clean_folder(artist, "Unknown Artist") / clean_folder(album, "Unknown Album")
    else:
        folder = out_root / clean_folder(primary_artist(eff["artist"]), "Unknown Artist") / "Singles"
    track = eff["tracknumber"].split("/")[0].strip()
    prefix = f"{int(track):02d} - " if track.isdigit() else ""
    return folder, prefix + clean_folder(eff["title"], "Untitled")


def plan_manual(row: dict, candidate: dict, out_root: Path,
                choices: dict[str, Choice] | None = None) -> Plan:
    source = Path(row["source"])
    if not source.exists():
        return Plan([PlannedOp(row, source, source, {}, overwrite=True, problem="source file not found")], [])
    local = read_local(source)
    values = resolve_values(local, provider_values(candidate, row), choices)
    folder, stem = manual_destination(local, values, Path(out_root))
    dest, collided = _unique_dest(folder, stem + source.suffix.lower(), set())
    op = PlannedOp(row, source, dest, {k: v for k, v in values.items() if v}, overwrite=True, collision=collided)
    for sc in _sidecars(source):
        target, _ = _unique_dest(folder, sc.name, {dest})
        op.sidecars.append((sc, target))
    return Plan([op], [])


def parse_overrides(text: str) -> dict[str, Choice]:
    """Parse ``title=l album=p genre=m:Punjabi date=k`` into per-field choices.

    l = local, p = provider, k = keep unchanged, m:<value> = type a value (quote it if it has spaces).
    Raises ValueError with a readable message on bad input.
    """
    import shlex

    modes = {"l": "local", "p": "provider", "k": "keep"}
    out: dict[str, Choice] = {}
    for token in shlex.split(text):
        field, sep, spec = token.partition("=")
        field = field.strip().lower()
        if not sep or field not in FIELDS:
            raise ValueError(f"unknown field in {token!r}; fields: {', '.join(FIELDS)}")
        if spec[:2].lower() == "m:":
            out[field] = {"mode": "manual", "value": spec[2:]}
        elif spec.lower() in modes:
            out[field] = {"mode": modes[spec.lower()]}
        else:
            raise ValueError(f"bad choice {spec!r} for {field}; use l, p, k or m:<value>")
    return out
