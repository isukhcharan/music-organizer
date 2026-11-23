"""Fix album-artist and track-number tags in place, for files that stay where they are (works on mp3/opus/flac/m4a).

Navidrome builds an album from (album name, album artist). Tracks of one album that disagree on the album artist
(blank on two of them, "Compilation" on the rest, a stray "Sukhcharan Singh", a different capital letter) therefore
show up as several duplicate albums. Downloads of a playlist also write the playlist position (104, 181, ...) as
the track number.

  scan_folder -> plan_albumfix (pure, no writes) -> apply_albumfix (journaled, undoable)

Rules (only album artist and track number are ever touched; audio is verified unchanged before the swap):
  * a consistent album is left alone, apart from merging spelling variants that differ only in capitals;
  * an inconsistent album in Soundtracks/Compilations gets "Compilation";
  * any other inconsistent album takes its dominant (>= half) related album artist;
  * a one-track album whose album artist has nothing to do with the track's artist gets the track's own artist;
  * a track number above 60 is a playlist position: it becomes the filename's number if that is unique, else blank.
"""
from __future__ import annotations

import os
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import mutagen

from .journal import Journal
from .repair import _TRACK_RE
from .text import norm, primary_artist

AUDIO = {".mp3", ".opus", ".ogg", ".flac", ".m4a"}
COMPILATION_DIRS = {"soundtracks", "compilations", "compilation"}
VARIOUS = {"compilation", "various artists", "various", "va"}
MAX_REAL_TRACK = 60
FIELDS = ("albumartist", "tracknumber")


@dataclass
class TagState:
    path: Path
    artists: str = ""
    albumartist: str = ""
    album: str = ""
    track: str = ""


@dataclass
class AlbumChange:
    path: Path
    set: dict[str, str | None] = field(default_factory=dict)     # field -> new value (None = remove)
    reasons: list[str] = field(default_factory=list)


def _first(m, key: str) -> str:
    vals = m.get(key) or []
    return str(vals[0]).strip() if vals else ""


def read_state(path: Path) -> TagState | None:
    try:
        m = mutagen.File(path, easy=True)
    except Exception:
        return None
    if m is None:
        return None
    return TagState(Path(path), artists=", ".join(str(a) for a in (m.get("artist") or [])),
                    albumartist=_first(m, "albumartist"), album=_first(m, "album"), track=_first(m, "tracknumber"))


def scan_folder(root: Path) -> tuple[list[TagState], list[Path]]:
    states, skipped = [], []
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and p.suffix.lower() in AUDIO:
            st = read_state(p)
            (states.append(st) if st else skipped.append(p))
    return states, skipped


def _related(albumartist: str, artists: str, folder_artist: str) -> bool:
    """Is this album artist plausibly the track's artist, the folder's artist, or a 'various artists' label?"""
    n = norm(albumartist)
    if not n:
        return False
    if n in VARIOUS:
        return True
    return n in norm(artists) or norm(artists) in n or n == norm(folder_artist) or n in norm(folder_artist)


def _majority_spelling(values: list[str]) -> dict[str, str]:
    """norm -> the most common exact spelling (ties: the first seen)."""
    spell: dict[str, Counter] = defaultdict(Counter)
    for v in values:
        if v:
            spell[norm(v)][v] += 1
    return {k: c.most_common(1)[0][0] for k, c in spell.items()}


def _track_number(raw: str) -> int | None:
    head = raw.split("/")[0].strip()
    return int(head) if head.isdigit() else None


def plan_albumfix(states: list[TagState]) -> tuple[list[AlbumChange], list[str]]:
    changes: dict[Path, AlbumChange] = {}
    notes: list[str] = []

    def change(path: Path) -> AlbumChange:
        return changes.setdefault(path, AlbumChange(path))

    albums: dict[tuple[Path, str], list[TagState]] = defaultdict(list)
    for s in states:
        albums[(s.path.parent, norm(s.album))].append(s)
    new_aa: dict[Path, str] = {}

    for (folder, _), group in albums.items():
        artist_dir = folder.parent
        folder_artist = artist_dir.name
        compilation = artist_dir.name.lower() in COMPILATION_DIRS
        distinct = {norm(s.albumartist) for s in group}
        # -- album artist --------------------------------------------------------------------------
        blank_split = compilation and "" in distinct and len({norm(s.artists) for s in group}) > 1
        if len(group) > 1 and (len(distinct) > 1 or blank_split):
            if compilation:
                target = next((s.albumartist for s in group if norm(s.albumartist) in VARIOUS), "Compilation")
                why = "album's tracks disagree on the album artist; compilation"
            else:
                valid = [s.albumartist for s in group if s.albumartist and _related(s.albumartist, s.artists, folder_artist)]
                counts = Counter(norm(v) for v in valid)
                top = counts.most_common(1)[0] if counts else None
                if top and top[1] / len(group) >= 0.5:
                    target = _majority_spelling(valid)[top[0]]
                    why = "album's tracks disagree on the album artist; using the dominant one"
                else:
                    target, why = None, ""
                    notes.append(f"{folder}: album artists disagree and none dominates "
                                 f"({sorted({s.albumartist or '(blank)' for s in group})}); left alone")
            if target:
                for s in group:
                    if s.albumartist != target:
                        new_aa[s.path] = target
                        change(s.path).reasons.append(f"album artist '{s.albumartist or '(blank)'}' -> '{target}' ({why})")
        elif len(group) == 1:
            s = group[0]
            if s.albumartist and not _related(s.albumartist, s.artists, folder_artist):
                target = primary_artist(s.artists)
                if target:
                    new_aa[s.path] = target
                    change(s.path).reasons.append(
                        f"album artist '{s.albumartist}' is unrelated to the track's artist -> '{target}'")
        # -- track numbers -------------------------------------------------------------------------
        prefixes = Counter((m.group(1).lstrip("0") or "0") for s in group for m in [_TRACK_RE.match(s.path.stem)] if m)
        for s in group:
            n = _track_number(s.track)
            if n is None or n <= MAX_REAL_TRACK:
                continue
            m = _TRACK_RE.match(s.path.stem)
            key = (m.group(1).lstrip("0") or "0") if m else None
            if m and 1 <= int(m.group(1)) <= MAX_REAL_TRACK and prefixes[key] == 1:
                change(s.path).set["tracknumber"] = str(int(m.group(1)))
                change(s.path).reasons.append(f"track number {n} is a playlist position -> {int(m.group(1))} (from filename)")
            else:
                change(s.path).set["tracknumber"] = None
                change(s.path).reasons.append(f"track number {n} is a playlist position -> removed")

    # -- spelling variants (capitals only) of an album artist inside one artist folder ----------------
    by_artist_dir: dict[Path, list[TagState]] = defaultdict(list)
    for s in states:
        by_artist_dir[s.path.parent.parent].append(s)
    for _, group in by_artist_dir.items():
        current = {s.path: new_aa.get(s.path, s.albumartist) for s in group}
        spell = _majority_spelling(list(current.values()))
        for s in group:
            cur = current[s.path]
            if cur and spell[norm(cur)] != cur:
                new_aa[s.path] = spell[norm(cur)]
                change(s.path).reasons.append(f"album artist spelling '{cur}' -> '{spell[norm(cur)]}'")

    out = []
    for s in states:
        ch = changes.get(s.path)
        if ch is None:
            continue
        if s.path in new_aa and new_aa[s.path] != s.albumartist:
            ch.set["albumartist"] = new_aa[s.path]
        if ch.set:
            out.append(ch)
    return out, notes


# -- applying ---------------------------------------------------------------------------------------
def _length(path: Path) -> float:
    return float(mutagen.File(path).info.length)


def _swap_in(path: Path, edit) -> None:
    """Edit a temp copy, check the audio is unchanged, then replace the original. The original survives any failure."""
    tmp = path.with_name(f".mo-tmp-{path.name}")
    try:
        expected = _length(path)
        shutil.copy2(path, tmp)
        m = mutagen.File(tmp, easy=True)
        edit(m)
        m.save()
        if abs(_length(tmp) - expected) > 0.01:
            raise OSError("audio duration changed while writing tags")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write(m, values: dict[str, str | None]) -> None:
    for key, val in values.items():
        if val is None:
            if key in m:
                del m[key]
        else:
            m[key] = [val]


def apply_albumfix(changes: list[AlbumChange], journal: Journal, job_id: str | None = None) -> str:
    job_id = job_id or journal.new_job_id()
    for ch in changes:
        op_id = journal.begin(job_id, "albumfix", ch.path, ch.path)
        try:
            snap: dict = {}

            def edit(m, ch=ch) -> None:
                for key in ch.set:                       # old values, to restore on undo
                    snap[key] = [str(v) for v in m[key]] if key in m else None
                _write(m, ch.set)

            _swap_in(ch.path, edit)
            journal.finish(op_id, "done", tags_before=snap, fields_written=list(ch.set))
        except Exception as exc:
            journal.finish(op_id, "failed", f"{type(exc).__name__}: {exc}")
    return job_id


def undo_albumfix(path: Path, snap: dict) -> None:
    def edit(m) -> None:
        for key, old in snap.items():
            if old is None:
                if key in m:
                    del m[key]
            else:
                m[key] = old

    _swap_in(Path(path), edit)
