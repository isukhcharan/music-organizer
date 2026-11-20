"""What counts as junk in a tag, and how provider genres map onto the library's own vocabulary.

Judgement calls live here so they are easy to read and edit:
  * junk  = rip-site spam and placeholders ("latest", "Downloaded from DjPunjab.Com", "Full Song", ...)
  * valid = any other genre text; kept exactly as the user has it
"""
from __future__ import annotations

import re

# Whole-value junk: matched against the normalised text of a genre or comment.
_JUNK_PATTERNS = [
    r"^latest( \w+)?$",                                  # "latest", "latest punjabi"
    r"\bdownloaded\b",                                   # "Downloaded From DjPunjab.Com"
    r"\bdj ?punjab\b|\bdj ?johal\b|\brisky ?jatt\b|\bpagalworld\b|\bmr ?jatt\b|\bsongs?\.?pk\b|\bdjmaza\b|\bbestwap\b",
    r"\bwww\b|https?:|\.(com|net|org|in|pk|co|info|me|cc)\b",   # URLs and site names
    r"^full songs?$|^mp3( songs?)?$|^songs?$|^new songs?$|^audio$|^music$",
    r"^unknown( genre)?$|^other$|^various$|^genre$|^none$|^n/?a$|^untitled$",  # placeholders
    r"^\d+$",                                            # bare numbers
]
_JUNK_RE = re.compile("|".join(f"(?:{p})" for p in _JUNK_PATTERNS), re.I)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\x00", " ")).strip().lower()


def is_junk(text: str) -> bool:
    """True for site names, URLs, 'latest', placeholder words. Empty text is not junk (it is just blank)."""
    t = _flat(text)
    return bool(t) and bool(_JUNK_RE.search(t))


def split_genre(values) -> list[str]:
    """Genre frames hold several values; some taggers also pack them as 'A;B' or 'A/B'. Keep the frame's list."""
    return [str(v).strip() for v in values if str(v).strip()]


def genre_is_invalid(values) -> bool:
    """A genre frame is invalid when any value in it is junk (the whole frame is then cleared)."""
    return any(is_junk(v) for v in split_genre(values))


# iTunes genre -> the library's own spelling. Anything not listed is ignored on purpose:
# "Worldwide", "New Age", "Soundtrack", "World" etc. are catch-alls that would make the library worse.
ITUNES_MAP = {
    "hip-hop/rap": "Hip Hop", "hip hop": "Hip Hop", "rap": "Hip Hop",
    "pop": "Pop", "indian pop": "Punjabi Pop", "punjabi pop": "Punjabi Pop", "punjabi": "Punjabi Pop",
    "bollywood": "Filmi", "filmi": "Filmi", "indian": "Filmi",
    "r&b/soul": "R&B", "rock": "Rock", "dance": "Dance", "electronic": "Electronic",
    "devotional & spiritual": "Devotional", "ghazals": "Ghazal", "sufi": "Sufi",
    "folk": "Folk", "indian folk": "Folk", "classical": "Classical", "hindustani classical": "Classical",
    "singer/songwriter": "Singer-Songwriter",
}


def map_itunes_genre(name: str) -> str | None:
    return ITUNES_MAP.get(_flat(name))
