"""Reading local tags and the "local first, provider fills blanks" write policy.

Read logic extracted from legacy read_local(); the write policy is the easy-tag loop in
legacy move_approved(). Reading never modifies a file.
"""
from __future__ import annotations

from pathlib import Path

from mutagen import File as MutagenFile

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}
GENERIC_FOLDERS = {"incoming", "music", "singles", "unknown"}
TAG_FIELDS = ("title", "artist", "albumartist", "album", "date", "tracknumber", "genre")


def _scalar(value) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def read_local(path: Path, root: Path | None = None) -> dict:
    """Local tags plus a weak album hint from the folder name.

    ``root`` is the scanned input folder: a file sitting directly in it has no folder hint
    (otherwise the input folder's own name, e.g. "Videos", would be searched as an album).
    """
    audio = MutagenFile(path, easy=True)
    tags = audio.tags if audio and audio.tags else {}

    def tag(key: str) -> str:
        v = tags.get(key, [])
        return _scalar(v[0]) if isinstance(v, list) and v else _scalar(v)

    folder_hint = path.parent.name
    if folder_hint.lower().startswith("album - "):
        folder_hint = folder_hint[8:].strip()
    if folder_hint.lower() in GENERIC_FOLDERS:
        folder_hint = ""
    if root is not None and path.parent.resolve() == Path(root).resolve():
        folder_hint = ""

    return {
        "title": tag("title") or path.stem,
        "artist": tag("artist"),
        "albumartist": tag("albumartist") or tag("album artist"),
        "album": tag("album"),
        "folder_album_hint": folder_hint,
        "date": tag("date") or tag("year"),
        "genre": tag("genre"),
        "tracknumber": tag("tracknumber"),
    }


def snapshot_tags(path: Path, fields) -> dict[str, list[str]]:
    """Current values of ``fields`` (``[]`` when a tag is absent). Read-only; used to make writes undoable."""
    audio = MutagenFile(path, easy=True)
    tags = audio.tags if audio is not None and audio.tags else {}
    return {f: [str(v) for v in (tags.get(f) or [])] for f in fields}


def restore_tags(path: Path, snapshot: dict[str, list[str]]) -> None:
    """Put tags back to a snapshot: absent-before tags are removed, others get their old values back."""
    audio = MutagenFile(path, easy=True)
    if audio is None or audio.tags is None:
        return
    for field, old in snapshot.items():
        if old:
            audio[field] = old
        elif field in audio:
            del audio[field]
    audio.save()


def write_tags(path: Path, values: dict) -> list[str]:
    """Write explicitly chosen values, overwriting existing tags. Only for manual decisions.

    A falsy value means "leave this tag alone" (never used to erase a tag).
    """
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported or unreadable audio file: {path}")
    if audio.tags is None:
        audio.add_tags()
    written = [k for k, v in values.items() if v]
    for key in written:
        audio[key] = [str(values[key])]
    if written:
        audio.save()
    return written


def fill_blank_tags(path: Path, values: dict) -> list[str]:
    """Write provider values into tags that are blank/missing. Never overwrites a populated tag.

    Returns the list of fields that were actually written.
    """
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError(f"Unsupported or unreadable audio file: {path}")
    if audio.tags is None:
        audio.add_tags()
    written: list[str] = []
    for key, value in values.items():
        if not value:
            continue
        existing = audio.get(key)
        items = existing if isinstance(existing, list) else ([existing] if existing is not None else [])
        if not any(str(item).strip() for item in items):
            audio[key] = [str(value)]
            written.append(key)
    if written:
        audio.save()
    return written
