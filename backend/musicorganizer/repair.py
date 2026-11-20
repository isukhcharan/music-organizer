"""Repair tags of files that stay where they are (e.g. an existing Navidrome library).

  scan_folder -> plan_repair (pure, no writes) -> apply_changes (journaled, undoable)

What it does, per mp3:
  * removes junk comments ("latest", "Downloaded from DjPunjab.Com", ...). Real comments are left alone.
  * clears a junk genre; a blank or cleared genre is then filled from the artist's own tagged tracks
    (consistent with the library) or, failing that, from an iTunes artist lookup. Otherwise it stays blank.
  * never touches a valid existing genre.
Writes go to a temp copy that is verified (same audio duration) and then swapped in, so a failure leaves
the original untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mutagen.id3 import COMM, ID3, TCON, TXXX, ID3NoHeaderError
from mutagen.mp3 import MP3

from .genres import genre_is_invalid, is_junk, map_itunes_genre, split_genre
from .journal import Journal
from .text import norm, primary_artist, sim

Lookup = Callable[[str], "str | None"]


@dataclass
class FileState:
    path: Path
    artist: str
    title: str
    genres: list[str]
    comments: list[tuple[str, list[str]]]      # (frame key, texts)


@dataclass
class Change:
    path: Path
    remove_comment_keys: list[str] = field(default_factory=list)
    clear_genre: bool = False
    new_genre: list[str] | None = None
    genre_source: str = ""                      # itunes-track | library-artist | itunes-artist
    replaced: list[str] | None = None           # the valid genre this overrides, if any
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.remove_comment_keys or self.clear_genre or self.new_genre)


def read_state(path: Path) -> FileState | None:
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        return None
    genres = split_genre(id3["TCON"].text) if "TCON" in id3 else []
    comments = []
    for key, frame in id3.items():
        if key.startswith("COMM") or (key.startswith("TXXX:") and frame.desc.lower() == "comment"):
            comments.append((key, [str(t) for t in frame.text]))
    artist = ""
    for fid in ("TPE2", "TPE1"):
        if fid in id3 and id3[fid].text:
            artist = str(id3[fid].text[0])
            break
    title = str(id3["TIT2"].text[0]) if "TIT2" in id3 and id3["TIT2"].text else Path(path).stem
    return FileState(Path(path), primary_artist(artist), title, genres, comments)


def scan_folder(root: Path) -> tuple[list[FileState], list[Path]]:
    states, skipped = [], []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".mp3":
            if p.suffix.lower() in {".flac", ".m4a", ".ogg", ".opus", ".wma", ".aac", ".wav"}:
                skipped.append(p)       # tag format not handled yet: reported, never touched
            continue
        st = read_state(p)
        (states.append(st) if st else skipped.append(p))
    return states, skipped


def artist_majority_genres(states: list[FileState], min_files: int = 2, min_share: float = 0.6) -> dict[str, list[str]]:
    """Per artist, the genre most of their *validly tagged* tracks already use."""
    by_artist: dict[str, Counter] = defaultdict(Counter)
    for s in states:
        if s.genres and not genre_is_invalid(s.genres) and s.artist:
            by_artist[norm(s.artist)][tuple(s.genres)] += 1
    out = {}
    for artist, counts in by_artist.items():
        total = sum(counts.values())
        genre, n = counts.most_common(1)[0]
        if total >= min_files and n / total >= min_share:
            out[artist] = list(genre)
    return out


# Genres specific enough to justify overriding what a file already says. "Pop" is deliberately absent:
# it is a catch-all, so it can fill a blank but never replaces a more specific existing genre.
SPECIFIC = {"Hip Hop", "Punjabi Pop", "Sufi", "Devotional", "Ghazal", "Filmi", "Folk", "Classical", "R&B",
            "Rock", "Electronic", "Dance", "Singer-Songwriter"}


def _same(a: list[str], b: list[str]) -> bool:
    return sorted(x.lower() for x in a) == sorted(x.lower() for x in b)


def plan_repair(states: list[FileState], artist_lookup: Lookup | None = None,
                track_lookup: Callable[[str, str], "str | None"] | None = None,
                use_library_majority: bool = True) -> list[Change]:
    """Decide the tag changes. Genre, in order of trust:

      1. the track's own iTunes genre (exact title + artist). Fills blanks/junk, and may *override* a valid
         existing genre when it is more specific (e.g. "Bhangra" -> "Hip Hop"/"Punjabi Pop"),
      2. blank/junk only: the artist's established genre from the library itself,
      3. blank/junk only: the artist's iTunes genre.
    A valid existing genre is otherwise never touched.
    """
    majority = artist_majority_genres(states) if use_library_majority else {}
    artist_cache: dict[str, str | None] = {}
    changes: list[Change] = []
    for s in states:
        ch = Change(s.path)
        for key, texts in s.comments:
            if any(is_junk(t) for t in texts):
                ch.remove_comment_keys.append(key)
        invalid = bool(s.genres) and genre_is_invalid(s.genres)
        blank_or_junk = not s.genres or invalid
        if invalid:
            ch.clear_genre = True

        track_g = track_lookup(s.artist, s.title) if track_lookup and s.artist else None
        if track_g and blank_or_junk:
            ch.new_genre, ch.genre_source = [track_g], "itunes-track"
        elif track_g and track_g in SPECIFIC and not _same(s.genres, [track_g]):
            ch.new_genre, ch.genre_source, ch.replaced = [track_g], "itunes-track", list(s.genres)
        elif blank_or_junk:
            akey = norm(s.artist)
            if akey in majority:
                ch.new_genre, ch.genre_source = majority[akey], "library-artist"
            elif artist_lookup and akey:
                if akey not in artist_cache:
                    artist_cache[akey] = artist_lookup(s.artist)
                if artist_cache[akey]:
                    ch.new_genre, ch.genre_source = [artist_cache[akey]], "itunes-artist"
        if ch.new_genre:
            ch.clear_genre = False          # replaced, not merely cleared
        if not ch.empty:
            changes.append(ch)
    return changes


# -- applying ---------------------------------------------------------------------------------------
def _snapshot(id3: ID3, ch: Change) -> dict:
    snap: dict = {"genre": split_genre(id3["TCON"].text) if "TCON" in id3 else None, "comments": []}
    for key in ch.remove_comment_keys:
        fr = id3.get(key)
        if fr is None:
            continue
        if key.startswith("COMM"):
            snap["comments"].append({"type": "COMM", "lang": fr.lang, "desc": fr.desc, "text": [str(t) for t in fr.text]})
        else:
            snap["comments"].append({"type": "TXXX", "desc": fr.desc, "text": [str(t) for t in fr.text]})
    return snap


def _save(id3: ID3, path: Path) -> None:
    id3.save(path, v2_version=3 if id3.version[1] == 3 else 4)


def _length(path: Path) -> float:
    return float(MP3(path).info.length)


def _swap_in(path: Path, edit: Callable[[ID3], None]) -> None:
    """Edit a temp copy, check the audio is unchanged, then replace the original. Original survives any failure."""
    tmp = path.with_name(f".mo-tmp-{path.name}")
    try:
        expected = _length(path)
        shutil.copy2(path, tmp)
        id3 = ID3(tmp)
        edit(id3)
        _save(id3, tmp)
        if abs(_length(tmp) - expected) > 0.01:
            raise OSError("audio duration changed while writing tags")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply_changes(changes: list[Change], journal: Journal, job_id: str | None = None) -> str:
    job_id = job_id or journal.new_job_id()
    for ch in changes:
        op_id = journal.begin(job_id, "retag", ch.path, ch.path)
        try:
            snap: dict = {}

            def edit(id3: ID3, ch=ch) -> None:
                snap.update(_snapshot(id3, ch))
                for key in ch.remove_comment_keys:
                    if key in id3:
                        del id3[key]
                if ch.clear_genre or ch.new_genre:
                    id3.delall("TCON")
                if ch.new_genre:
                    id3.add(TCON(encoding=3, text=ch.new_genre))

            _swap_in(ch.path, edit)
            journal.finish(op_id, "done", tags_before=snap, fields_written=["comment", "genre"])
        except Exception as exc:
            journal.finish(op_id, "failed", f"{type(exc).__name__}: {exc}")
    return job_id


def undo_retag(path: Path, snap: dict) -> None:
    """Put back exactly the genre and comment frames the repair removed or replaced."""
    def edit(id3: ID3) -> None:
        id3.delall("TCON")
        if snap.get("genre"):
            id3.add(TCON(encoding=3, text=snap["genre"]))
        for c in snap.get("comments", []):
            if c["type"] == "COMM":
                id3.add(COMM(encoding=3, lang=c["lang"], desc=c["desc"], text=c["text"]))
            else:
                id3.add(TXXX(encoding=3, desc=c["desc"], text=c["text"]))

    _swap_in(Path(path), edit)


# -- iTunes artist lookup ---------------------------------------------------------------------------
class TrackGenreLookup:
    """iTunes genre for one exact song (title and artist must both match), cached on disk.

    The cache means re-running a preview, or an interrupted run, never repeats a request.
    """

    def __init__(self, cache_path: Path | None = None, delay: float = 3.0):
        self.path, self.delay = cache_path, delay
        self.cache: dict[str, str | None] = {}
        if cache_path and Path(cache_path).exists():
            try:
                self.cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}

    def __call__(self, artist: str, title: str) -> str | None:
        key = f"{norm(artist)}|{norm(title)}"
        if key in self.cache:
            return self.cache[key]
        time.sleep(self.delay)
        genre = None
        try:
            url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
                {"term": f"{title} {artist}", "entity": "song", "limit": 8})
            req = urllib.request.Request(url, headers={"User-Agent": "music-organizer/0.1"})
            for r in json.load(urllib.request.urlopen(req, timeout=20)).get("results", []):
                if sim(title, r.get("trackName", "")) >= 0.9 and norm(artist) in norm(r.get("artistName", "")):
                    genre = map_itunes_genre(r.get("primaryGenreName", ""))
                    break
        except Exception:
            return None                     # network trouble: not cached, so it is retried next run
        self.cache[key] = genre
        if self.path:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.path).write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
        return genre


def itunes_artist_genre(artist: str, delay: float = 3.0) -> str | None:
    """Most common mapped genre across the artist's songs on iTunes (needs >=2 songs and >=50% agreement).

    One request per artist, so it stays under iTunes' rate limit. Returns None when unsure.
    """
    time.sleep(delay)
    try:
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
            {"term": artist, "entity": "song", "attribute": "artistTerm", "limit": 50})
        req = urllib.request.Request(url, headers={"User-Agent": "music-organizer/0.1"})
        results = json.load(urllib.request.urlopen(req, timeout=20)).get("results", [])
    except Exception:
        return None
    want = norm(artist)
    counts: Counter = Counter()
    for r in results:
        if want and want in norm(r.get("artistName", "")):
            g = map_itunes_genre(r.get("primaryGenreName", ""))
            if g:
                counts[g] += 1
    total = sum(counts.values())
    if total >= 2:
        genre, n = counts.most_common(1)[0]
        if n / total >= 0.5:
            return genre
    return None
