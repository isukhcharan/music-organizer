"""Text normalisation and similarity helpers.

Extracted unchanged (behaviour-wise) from legacy/music_organizer_v15_move.py.
Pure functions: no I/O, no network.
"""
from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher

_AUDIO_EXT_RE = re.compile(r"\.(mp3|flac|m4a|aac|ogg|opus|wav|wma)$", re.I)


def norm(value) -> str:
    value = html.unescape(str(value or "")).strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = _AUDIO_EXT_RE.sub("", value)
    value = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", value)
    value = re.sub(r"\([^)]*(official|lyrics?|video|audio|4k|1080p)[^)]*\)", " ", value, flags=re.I)
    value = re.sub(r"\[[^\]]*(official|lyrics?|video|audio|4k|1080p)[^]]*\]", " ", value, flags=re.I)
    value = re.sub(r"[(\[]\s*(?:feat|ft|featuring|with)\b[^)\]]*[)\]]", " ", value, flags=re.I)
    value = re.sub(r"[_\-]+", " ", value)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.lower())).strip()


def sim(a, b) -> float:
    a, b = norm(a), norm(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def album_identity_norm(value) -> str:
    """Remove generic soundtrack/release wording before comparing album identities."""
    value = norm(value)
    value = re.sub(
        r"\b(original motion picture soundtrack|motion picture soundtrack|original soundtrack|soundtrack|ost)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def album_identity_score(a, b) -> float:
    a_core, b_core = album_identity_norm(a), album_identity_norm(b)
    return SequenceMatcher(None, a_core, b_core).ratio() if a_core and b_core else 0.0


def artist_core(value) -> str:
    value = html.unescape(str(value or "")).strip()
    return value.split(" - ", 1)[0].strip()


def artist_parts(value) -> list[str]:
    """Split common artist-credit separators while preserving full individual names."""
    value = html.unescape(str(value or "")).strip()
    if not value:
        return []
    parts = re.split(
        r"\s*/\s*|\s*,\s*|\s+&\s+|\s+and\s+|\s+ft\.?\s+|\s+feat\.?\s+|\s+featuring\s+",
        value,
        flags=re.I,
    )
    return [x.strip() for x in parts if x.strip()]


def artist_match_score(local_artist, remote_artist) -> float:
    """Best pairwise artist-credit similarity; avoids penalising extra collaborators."""
    left, right = artist_parts(local_artist), artist_parts(remote_artist)
    if not left or not right:
        return 0.0
    best = 0.0
    for a in left:
        for b in right:
            score = sim(a, b)
            na, nb = norm(a), norm(b)
            if na and nb and (na == nb or na in nb or nb in na):
                score = max(score, 0.90)
            best = max(best, score)
    return best


def primary_artist(value) -> str:
    value = str(value or "").strip()
    value = re.split(r"\s*/\s*|\s*,\s*|\s+ft\.?\s+|\s+feat\.?\s+", value, maxsplit=1, flags=re.I)[0]
    return value.strip()


def tracknumber_value(value) -> int | None:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def clean_folder(value, fallback: str) -> str:
    """Make a string safe as a single path component (Windows-safe superset)."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(value or "")).strip(" .")
    return value[:100] or fallback
