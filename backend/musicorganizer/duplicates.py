"""Byte-identical duplicate detection. Never deletes anything; it only reports."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DuplicateTracker:
    """First file seen with a given hash is the original; later identical files are duplicates."""

    def __init__(self):
        self._seen: dict[str, str] = {}

    def check(self, path: Path) -> str | None:
        """Return the path of the original if ``path`` is a duplicate, else register it and return None."""
        digest = sha256(path)
        if digest in self._seen:
            return self._seen[digest]
        self._seen[digest] = str(path)
        return None
