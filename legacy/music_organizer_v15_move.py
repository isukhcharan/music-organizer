#!/usr/bin/env python3
"""
Music Organizer v15
- Preview by default; --apply moves approved files to C:\\MusicOrganizer\\Output.
- Never edits source files.
- Uses YouTube Music first, with JioSaavn as a last-resort fallback.
- Prefers the release matching the existing local album tag.
- Requires a strong meaningful album-title match; generic soundtrack wording is ignored.
- Uses verified provider track positions for output filenames, but preserves populated local track-number tags.
- Blocks ambiguous album identities and byte-identical duplicates.
- Preserves every populated local metadata field; matched provider metadata fills only blank/missing fields.
- Resolves album-artist folders at album level from provider track artists, with conservative compilation grouping.
- Never uses a Various Artists folder.
"""

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import time
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from musicapy.saavn_api.api import SaavnAPI
from ytmusicapi import YTMusic

DEFAULT_ROOT = Path(r"C:\MusicTest\Incoming")
BASE = Path(r"C:\MusicOrganizer")
OUT = BASE / "Output"
DATA = BASE / "data"
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}
PAUSE = 0.20
AUTO_SCORE = 0.90
MIN_GAP = 0.045
api = SaavnAPI()
yt = YTMusic()


def norm(value):
    value = html.unescape(str(value or "")).strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"\.(mp3|flac|m4a|aac|ogg|opus|wav|wma)$", "", value, flags=re.I)
    value = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", value)
    value = re.sub(r"\([^)]*(official|lyrics?|video|audio|4k|1080p)[^)]*\)", " ", value, flags=re.I)
    value = re.sub(r"\[[^\]]*(official|lyrics?|video|audio|4k|1080p)[^]]*\]", " ", value, flags=re.I)
    value = re.sub(r"[_\-]+", " ", value)
    return re.sub(r"[^\w\s]", " ", value.lower()).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def album_identity_norm(value):
    """Remove generic soundtrack/release wording before comparing album identities."""
    value = norm(value)
    value = re.sub(r"\b(original motion picture soundtrack|motion picture soundtrack|original soundtrack|soundtrack|ost)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def album_identity_score(a, b):
    a_core, b_core = album_identity_norm(a), album_identity_norm(b)
    return SequenceMatcher(None, a_core, b_core).ratio() if a_core and b_core else 0.0


def scalar(v):
    if isinstance(v, (str, int, float)):
        return str(v).strip()
    return ""


def first(d, *keys):
    if not isinstance(d, dict):
        return ""
    for k in keys:
        v = scalar(d.get(k))
        if v:
            return html.unescape(v)
    return ""


def list_payload(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("data", "results", "songs", "albums", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [x for x in nested if isinstance(x, dict)]
            if isinstance(nested, dict):
                found = list_payload(nested)
                if found:
                    return found
    return []


def artist_core(value):
    value = html.unescape(str(value or "")).strip()
    return value.split(" - ", 1)[0].strip()


def artist_parts(value):
    """Split common artist-credit separators while preserving full individual names."""
    value = html.unescape(str(value or "")).strip()
    if not value:
        return []
    parts = re.split(r"\s*/\s*|\s*,\s*|\s+&\s+|\s+and\s+|\s+ft\.?\s+|\s+feat\.?\s+|\s+featuring\s+", value, flags=re.I)
    return [x.strip() for x in parts if x.strip()]


def artist_match_score(local_artist, remote_artist):
    """Best pairwise artist-credit similarity; avoids penalizing extra collaborators."""
    left, right = artist_parts(local_artist), artist_parts(remote_artist)
    if not left or not right:
        return 0.0
    best = 0.0
    for a in left:
        for b in right:
            score = sim(a, b)
            # Exact normalized token inclusion handles punctuation and credit formatting.
            na, nb = norm(a), norm(b)
            if na and nb and (na == nb or na in nb or nb in na):
                score = max(score, 0.90)
            best = max(best, score)
    return best


def read_local(path):
    audio = MutagenFile(path, easy=True)
    tags = audio.tags if audio and audio.tags else {}

    def tag(k):
        v = tags.get(k, [])
        return scalar(v[0]) if isinstance(v, list) and v else scalar(v)

    # Use folder name as a weak album hint only when it looks like an album folder.
    folder_hint = path.parent.name
    if folder_hint.lower().startswith("album - "):
        folder_hint = folder_hint[8:].strip()
    if folder_hint.lower() in {"incoming", "music", "singles", "unknown"}:
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


def search_candidates(local, path):
    """YouTube Music song search first; JioSaavn only if YT Music has no usable results."""
    title = local["title"] or path.stem
    artist = local["artist"] or local["albumartist"]
    query = " ".join(x for x in (title, artist) if x).strip()
    candidates = []
    try:
        for item in yt.search(query, filter="songs")[:10]:
            remote_title = item.get("title", "")
            remote_artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
            album_obj = item.get("album") or {}
            remote_album = album_obj.get("name", "") if isinstance(album_obj, dict) else ""
            ts, ars = sim(title, remote_title), artist_match_score(artist, remote_artists) if artist else 0.0
            candidates.append({"source":"ytmusic", "score":round(.70*ts+.30*ars,4),
                "title_score":round(ts,4), "artist_score":round(ars,4), "title":remote_title,
                "artist":remote_artists, "artist_raw":remote_artists, "id":item.get("videoId", ""),
                "url":"", "year":"", "album_hint":remote_album,
                "browse_id":album_obj.get("id", "") if isinstance(album_obj,dict) else ""})
    except Exception as exc:
        print(f"  YouTube Music song search warning: {type(exc).__name__}: {exc}")
    if candidates:
        return sorted(candidates, key=lambda x:x["score"], reverse=True)
    # Least-priority fallback retained from v7.
    try:
        raw = api.search_song(query)
        time.sleep(PAUSE)
        for item in list_payload(raw):
            remote_title = first(item, "song", "title", "name")
            remote_artist_raw = first(item, "primary_artists", "artist", "subtitle")
            remote_artist = artist_core(remote_artist_raw)
            if not remote_title: continue
            ts, ars = sim(title, remote_title), artist_match_score(artist, remote_artist) if artist else 0.0
            candidates.append({"source":"jiosaavn", "score":round(.70*ts+.30*ars,4),
                "title_score":round(ts,4), "artist_score":round(ars,4), "title":remote_title,
                "artist":remote_artist, "artist_raw":remote_artist_raw,
                "id":first(item,"id","songid","song_id"), "url":first(item,"perma_url","url","song_url"),
                "year":first(item,"year","release_year"), "album_hint":first(item,"album","album_name")})
    except Exception as exc:
        print(f"  JioSaavn fallback warning: {type(exc).__name__}: {exc}")
    unique={}
    for c in candidates: unique.setdefault(c.get("id") or (c["title"],c["artist"],c.get("album_hint")),c)
    return sorted(unique.values(),key=lambda x:x["score"],reverse=True)

def call_song_details(candidate):
    identifier = None
    if candidate.get("url"):
        identifier = api.create_identifier(candidate["url"], "song")
    elif candidate.get("id"):
        identifier = api.create_identifier(candidate["id"], "song")
    if identifier is None:
        return {}
    result = api.get_song_details(identifier)
    time.sleep(PAUSE)
    if isinstance(result, list):
        result = result[0] if result and isinstance(result[0], dict) else {}
    if not isinstance(result, dict):
        return {}
    if candidate.get("id") in result and isinstance(result[candidate["id"]], dict):
        result = result[candidate["id"]]
    if isinstance(result.get("data"), dict):
        result = result["data"]
    return result


def album_identifier(song):
    url = first(song, "album_url", "perma_url_album", "album_link")
    aid = first(song, "albumid", "album_id")
    album_obj = song.get("album")
    if isinstance(album_obj, dict):
        url = url or first(album_obj, "url", "perma_url")
        aid = aid or first(album_obj, "id", "albumid", "album_id")
    if url:
        return api.create_identifier(url, "album")
    if aid:
        return api.create_identifier(aid, "album")
    return None


def unwrap_album(value):
    if isinstance(value, list):
        value = value[0] if value and isinstance(value[0], dict) else {}
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("data"), dict):
        value = value["data"]
    if "songs" not in value and "tracklist" not in value:
        for d in value.values():
            if isinstance(d, dict) and ("songs" in d or "tracklist" in d):
                return d
    return value


def get_album(song):
    ident = album_identifier(song)
    if ident is None:
        return {}
    try:
        result = api.get_album_details(ident)
        time.sleep(PAUSE)
        return unwrap_album(result)
    except Exception:
        return {}


def get_track_list(album):
    for key in ("songs", "tracks", "tracklist", "items"):
        value = album.get(key) if isinstance(album, dict) else None
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def verify_track(local_title, tracks):
    ranked = []
    for idx, track in enumerate(tracks, 1):
        title = first(track, "song", "title", "name")
        if title:
            ranked.append((sim(local_title, title), idx, title, track))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0
    if best[0] >= 0.90 and best[0] - runner >= 0.035:
        explicit = first(best[3], "sequence", "track_number", "tracknumber", "position")
        number = explicit if explicit.isdigit() and int(explicit) > 0 else str(best[1])
        return {"score": round(best[0], 4), "number": number, "title": best[2]}
    return None


def tracknumber_value(value):
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def clean_folder(value, fallback):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(value or "")).strip(" .")
    return (value[:100] or fallback)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_missing_mp3_tags(path, values):
    audio = ID3(path)
    frames = [
        ("TIT2", TIT2, values.get("title")),
        ("TPE1", TPE1, values.get("artist")),
        ("TPE2", TPE2, values.get("albumartist")),
        ("TALB", TALB, values.get("album")),
        ("TDRC", TDRC, values.get("date")),
        ("TRCK", TRCK, values.get("tracknumber")),
        ("TCON", TCON, values.get("genre")),
    ]
    for frame_id, frame_type, value in frames:
        existing = audio.getall(frame_id)
        existing_text = str(existing[0].text[0]).strip() if existing and getattr(existing[0], "text", None) else ""
        if value and not existing_text:
            audio.add(frame_type(encoding=3, text=str(value)))
    audio.save(path)


def set_verified_mp3_tracknumber(path, tracknumber):
    """Set the verified track number on the OUTPUT COPY, never on the source."""
    audio = ID3(path)
    audio.delall("TRCK")
    audio.add(TRCK(encoding=3, text=str(tracknumber)))
    audio.save(path)


def resolve_one(candidate, local):
    if candidate.get("source")=="ytmusic":
        album={}
        tracks=[]
        if candidate.get("browse_id"):
            try:
                album=yt.get_album(candidate["browse_id"])
                for idx,t in enumerate(album.get("tracks",[]),1):
                    artists=", ".join(a.get("name","") for a in t.get("artists",[]) if a.get("name"))
                    tracks.append({"song":t.get("title",""),"primary_artists":artists,
                        "sequence":str(t.get("trackNumber") or idx)})
            except Exception: pass
        album_title=(album.get("title") if album else "") or candidate.get("album_hint","")
        verified=verify_track(local["title"],tracks) if tracks else None
        return {"album":album_title,"album_artist":"","year":str(album.get("year") or "") if album else "",
            "tracks":tracks,"verified":verified}, ("" if verified else "YouTube Music result found, but track could not be verified in its album tracklist")
    song=call_song_details(candidate)
    if not song:return None,"Song details unavailable"
    album=get_album(song); tracks=get_track_list(album)
    album_title=first(album,"title","name") or first(song,"album") or candidate.get("album_hint","")
    album_artist=first(album,"album_artist","albumartist")
    year=first(album,"year","release_year") or first(song,"year","release_year") or candidate.get("year","")
    verified=verify_track(local["title"],tracks) if tracks else None
    return {"album":album_title,"album_artist":album_artist,"year":year,"tracks":tracks,"verified":verified},("" if verified else "Tracklist verification unavailable")



def album_search_candidates(album_name, artist_name):
    """Search YouTube Music albums first; only use JioSaavn if YT returns none."""
    query = " ".join(x for x in (album_name, artist_name) if x).strip()
    if not query: return []
    found=[]
    try:
        for item in yt.search(query, filter="albums")[:10]:
            title=item.get("title","")
            artist=", ".join(a.get("name","") for a in item.get("artists",[]) if a.get("name"))
            bid=item.get("browseId","")
            if not title or not bid: continue
            found.append({"source":"ytmusic","title":title,"artist":artist,"artist_raw":artist,
                "browse_id":bid,"url":"","id":bid,"title_score":sim(album_name,title),
                "identity_score":album_identity_score(album_name,title),
                "artist_score":sim(artist_name,artist) if artist_name and artist else .5})
    except Exception as exc:
        print(f"  YouTube Music album search warning: {type(exc).__name__}: {exc}")
    if found: return sorted(found,key=lambda x:(x["identity_score"],x["artist_score"]),reverse=True)
    try:
        raw=api.search_album(query); time.sleep(PAUSE)
        for item in list_payload(raw):
            title=first(item,"title","name","album"); artist=first(item,"primary_artists","artist","subtitle")
            url=first(item,"perma_url","url","album_url"); aid=first(item,"id","albumid","album_id")
            if title and (url or aid):
                found.append({"source":"jiosaavn","title":title,"artist":artist_core(artist),"artist_raw":artist,
                    "url":url,"id":aid,"title_score":sim(album_name,title),"identity_score":album_identity_score(album_name,title),
                    "artist_score":sim(artist_name,artist_core(artist)) if artist_name and artist else .5})
    except Exception as exc: print(f"  JioSaavn album fallback warning: {type(exc).__name__}: {exc}")
    return sorted(found,key=lambda x:(x["identity_score"],x["artist_score"]),reverse=True)

def resolve_album_candidate(candidate):
    try:
        if candidate.get("source")=="ytmusic":
            album=yt.get_album(candidate["browse_id"])
            tracks=[]
            for idx,t in enumerate(album.get("tracks",[]),1):
                artists=", ".join(a.get("name","") for a in t.get("artists",[]) if a.get("name"))
                tracks.append({"song":t.get("title",""),"primary_artists":artists,
                    "sequence":str(t.get("trackNumber") or idx),"duration":t.get("duration","")})
            return {"album":album.get("title") or candidate["title"],"album_artist":"",
                "provider_artist":", ".join(a.get("name","") for a in album.get("artists",[]) if a.get("name")),
                "year":str(album.get("year") or ""),"tracks":tracks}
        ident=api.create_identifier(candidate["url"],"album") if candidate.get("url") else api.create_identifier(candidate["id"],"album")
        if ident is None:return {}
        result=api.get_album_details(ident); time.sleep(PAUSE); album=unwrap_album(result)
        if not album:return {}
        return {"album":first(album,"title","name") or candidate["title"],
            "album_artist":first(album,"album_artist","albumartist"),
            "provider_artist":first(album,"primary_artists","artist") or candidate.get("artist",""),
            "year":first(album,"year","release_year"),"tracks":get_track_list(album)}
    except Exception as exc:
        print(f"  Album details warning ({candidate.get('source')}): {type(exc).__name__}: {exc}")
        return {}

def match_local_to_track(local, tracks, used_positions):
    """Match one local file to one unique provider track; never assign one track twice."""
    ranked = []
    for idx, track in enumerate(tracks, 1):
        explicit = first(track, "sequence", "track_number", "tracknumber", "position")
        number = int(explicit) if explicit.isdigit() and int(explicit) > 0 else idx
        if number in used_positions:
            continue
        title = first(track, "song", "title", "name")
        if not title:
            continue
        title_score = sim(local["title"], title)
        local_artist = local.get("artist", "")
        remote_artist = first(track, "primary_artists", "singers", "artist")
        artist_score = artist_match_score(local_artist, remote_artist) if local_artist and remote_artist else 0.0
        # Artist credit must agree when both sides provide one; title alone is insufficient.
        score = 0.72 * title_score + 0.28 * artist_score
        ranked.append((score, title_score, number, title, track))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0
    # Slightly looser title threshold than song search: album context is already established.
    if best[1] >= 0.84 and best[4] and (not local.get("artist") or not first(best[4], "primary_artists", "singers", "artist") or artist_match_score(local.get("artist"), first(best[4], "primary_artists", "singers", "artist")) >= 0.62) and (best[0] - runner >= 0.025 or best[1] >= 0.98):
        return {"score": round(best[0], 4), "title_score": round(best[1], 4),
                "number": best[2], "title": best[3], "artist": first(best[4], "primary_artists", "singers", "artist")}
    return None


def process_album_group(paths, duplicate_hashes):
    """Resolve one tagged album as a unit, then match every local file against its tracklist."""
    locals_by_path = {p: read_local(p) for p in paths}
    first_local = next(iter(locals_by_path.values()))
    album_name = first_local.get("album") or first_local.get("folder_album_hint") or ""
    # Use a consistent album artist if available; otherwise the first track artist is a search hint only.
    album_artists = [v["albumartist"] for v in locals_by_path.values() if v.get("albumartist")]
    artist_hint = max(set(album_artists), key=album_artists.count) if album_artists else ""
    if not artist_hint:
        track_artists = [v["artist"] for v in locals_by_path.values() if v.get("artist")]
        artist_hint = max(set(track_artists), key=track_artists.count) if track_artists else ""

    candidates = album_search_candidates(album_name, artist_hint)
    # Filter by album identity first. Artist matching is supporting evidence, not mandatory
    # because soundtrack album credits are often inconsistent.
    # Generic soundtrack wording must not make unrelated film albums look similar.
    # Require a strong match on the meaningful album title itself.
    candidates = [c for c in candidates if c["identity_score"] >= 0.82 and
                  (c["artist_score"] >= 0.35 or not artist_hint)]
    resolved = []
    for candidate in candidates[:5]:
        detail = resolve_album_candidate(candidate)
        if detail and detail.get("tracks"):
            detail["candidate"] = candidate
            resolved.append(detail)

    # Choose the release with best album identity and greatest coverage of the local files.
    evaluated = []
    for detail in resolved:
        used, matches = set(), {}
        for path, local in locals_by_path.items():
            match = match_local_to_track(local, detail["tracks"], used)
            if match:
                matches[path] = match
                used.add(match["number"])
        c = detail["candidate"]
        coverage = len(matches) / max(1, len(paths))
        identity = 0.65 * c["identity_score"] + 0.15 * c["artist_score"] + 0.20 * coverage
        evaluated.append((identity, coverage, detail, matches))
    evaluated.sort(key=lambda x: (x[0], x[1]), reverse=True)

    chosen = None
    if evaluated:
        best = evaluated[0]
        second_score = evaluated[1][0] if len(evaluated) > 1 else 0
        if best[0] >= 0.70 and (best[0] - second_score >= 0.025 or best[1] >= 0.85):
            chosen = best

    rows = []
    for path, local in locals_by_path.items():
        row = {
            "source": str(path), "local_title": local["title"], "local_artist": local["artist"],
            "local_albumartist": local["albumartist"], "local_album": local["album"],
            "folder_album_hint": local["folder_album_hint"], "status": "REVIEW",
            "score": 0, "matched_title": "", "matched_artist": "", "matched_album": "",
            "matched_albumartist": "", "matched_year": "", "provider_id": "", "provider_url": "",
            "track_number": "", "release_track_match": "", "duplicate_of": "", "output": "",
            "detail_status": "", "candidates": json.dumps(candidates[:8], ensure_ascii=False),
            "match_method": "album-first",
        }
        digest = sha256(path)
        if digest in duplicate_hashes:
            row["status"] = "REVIEW_DUPLICATE"
            row["duplicate_of"] = duplicate_hashes[digest]
            row["detail_status"] = "Byte-identical copy of another input file; keep one copy"
            rows.append(row)
            continue
        duplicate_hashes[digest] = str(path)

        if not chosen:
            row["detail_status"] = "Could not confidently identify the tagged album and its tracklist"
            rows.append(row)
            continue

        _, coverage, detail, matches = chosen
        cand = detail["candidate"]
        match = matches.get(path)
        row.update({
            "score": round(chosen[0], 4), "matched_album": detail["album"],
            "matched_albumartist": detail["album_artist"],
            "matched_artist": detail["provider_artist"], "matched_year": detail["year"],
            "provider_id": cand.get("id", ""), "provider_url": cand.get("url", ""),
        })
        if not match:
            row["detail_status"] = "Album identified, but this local track did not uniquely match a track on its tracklist"
            rows.append(row)
            continue
        existing_track = tracknumber_value(local.get("tracknumber"))
        conflict_note = (f" Local track-number tag {existing_track} will be preserved; provider album position {match['number']} will be used for output filename ordering."
                         if existing_track and existing_track != match["number"] else "")
        row.update({
            "status": "AUTO_MATCH", "matched_title": match["title"],
            "matched_artist": match.get("artist") or detail["provider_artist"],
            "track_number": str(match["number"]), "release_track_match": match["title"],
            "detail_status": f"Matched against album tracklist ({len(detail['tracks'])} tracks); album coverage {coverage:.0%}.{conflict_note}",
        })
        rows.append(row)
    return rows

def process(path, apply, duplicate_hashes):
    local = read_local(path)
    candidates = search_candidates(local, path)
    row = {
        "source": str(path), "local_title": local["title"], "local_artist": local["artist"],
        "local_albumartist": local["albumartist"], "local_album": local["album"],
        "folder_album_hint": local["folder_album_hint"], "status": "REVIEW",
        "score": 0, "matched_title": "", "matched_artist": "", "matched_album": "",
        "matched_albumartist": "", "matched_year": "", "provider_id": "", "provider_url": "",
        "track_number": "", "release_track_match": "", "duplicate_of": "", "output": "",
        "detail_status": "", "candidates": json.dumps(candidates[:8], ensure_ascii=False),
    }
    if not candidates:
        row["status"] = "NO_RESULT"
        row["detail_status"] = "No search result"
        return row

    # Exact byte duplicates are not processed twice.
    digest = sha256(path)
    if digest in duplicate_hashes:
        row["status"] = "REVIEW_DUPLICATE"
        row["duplicate_of"] = duplicate_hashes[digest]
        row["detail_status"] = "Byte-identical copy of another input file; keep one copy"
        return row
    duplicate_hashes[digest] = str(path)

    title_ok = candidates[0]["title_score"] >= 0.90
    if not title_ok:
        row["detail_status"] = "Top search title is not a close enough match"
        return row

    # A close title with a different credited artist is not safe to auto-approve.
    local_artist = local.get("artist") or local.get("albumartist")
    artist_checked = [c for c in candidates if not local_artist or not c.get("artist") or artist_match_score(local_artist, c.get("artist")) >= 0.62]
    if not artist_checked:
        row["detail_status"] = "Song title matched, but provider artist credit does not agree with local artist; manual review required"
        row["candidates"] = json.dumps(candidates[:8], ensure_ascii=False)
        return row

    # Resolve strong candidates so the existing album tag can disambiguate releases.
    pool = [c for c in artist_checked[:4] if c["title_score"] >= 0.90 and c["score"] >= artist_checked[0]["score"] - 0.12]
    resolved = []
    for candidate in pool:
        try:
            detail, error = resolve_one(candidate, local)
            if detail:
                album_hint = local["album"] or local["folder_album_hint"]
                album_similarity = sim(album_hint, detail["album"]) if album_hint and detail["album"] else 0
                detail["album_similarity"] = album_similarity
                detail["candidate"] = candidate
                detail["error"] = error
                resolved.append(detail)
        except Exception as exc:
            continue

    if not resolved:
        row["detail_status"] = "Could not resolve song/release details"
        return row

    # If an album is already tagged, only select a release that agrees with it.
    album_hint = local["album"] or local["folder_album_hint"]
    if album_hint:
        compatible = [r for r in resolved if r["album"] and r["album_similarity"] >= 0.88]
        if not compatible:
            best = max(resolved, key=lambda r: r["candidate"]["score"])
            row.update({
                "score": best["candidate"]["score"], "matched_title": best["candidate"]["title"],
                "matched_artist": best["candidate"]["artist"], "matched_album": best["album"],
                "matched_albumartist": best["album_artist"], "matched_year": best["year"],
                "provider_id": best["candidate"]["id"], "provider_url": best["candidate"]["url"],
                "detail_status": f"Release conflict: local album/folder says '{album_hint}', provider release says '{best['album']}'",
                "status": "REVIEW_RELEASE_CONFLICT",
            })
            return row
        resolved = compatible
        resolved.sort(key=lambda r: (r["album_similarity"], r["candidate"]["score"]), reverse=True)
    else:
        resolved.sort(key=lambda r: r["candidate"]["score"], reverse=True)

    chosen = resolved[0]
    cand = chosen["candidate"]
    if len(resolved) > 1:
        first_score = chosen["candidate"]["score"]
        second_score = resolved[1]["candidate"]["score"]
        if abs(first_score - second_score) < MIN_GAP and abs(chosen["album_similarity"] - resolved[1]["album_similarity"]) < 0.08:
            row["detail_status"] = "Multiple equally plausible releases; needs a manual choice"
            return row

    verified = chosen.get("verified")
    row.update({
        "score": cand["score"], "matched_title": cand["title"], "matched_artist": cand["artist"],
        "matched_album": chosen["album"], "matched_albumartist": chosen["album_artist"],
        "matched_year": chosen["year"], "provider_id": cand["id"], "provider_url": cand["url"],
    })
    if not verified:
        row["detail_status"] = chosen["error"] or "Could not uniquely verify title in release tracklist"
        return row

    provider_track = int(verified["number"])
    existing_track = tracknumber_value(local["tracknumber"])
    if existing_track and existing_track != provider_track:
        detail = (f"Verified in {len(chosen['tracks'])}-track release; provider artist and title checked. "
                  f"Local track-number tag {existing_track} will be preserved; provider album position "
                  f"{provider_track} will be used for output filename ordering.")
    else:
        detail = f"Verified in {len(chosen['tracks'])}-track release; provider artist and title checked"

    row.update({
        "status": "AUTO_MATCH", "track_number": str(provider_track),
        "release_track_match": verified["title"],
        "detail_status": detail,
    })

    return row



def album_group_key(row):
    album = row.get("local_album") or row.get("matched_album") or row.get("folder_album_hint") or "Singles"
    # Group album tracks by album title; this is used only to infer a safe folder-level artist.
    parent = Path(row.get("source", "")).parent.name
    return (norm(parent), norm(album))


def primary_artist(value):
    value = str(value or "").strip()
    # Existing local artist strings in this library commonly separate credits with '/'.
    value = re.split(r"\s*/\s*|\s*,\s*|\s+ft\.?\s+|\s+feat\.?\s+", value, maxsplit=1, flags=re.I)[0]
    return value.strip()


def infer_album_artists(report):
    """Resolve folder-level album artists across all approved tracks, not per track."""
    groups = defaultdict(list)
    for row in report:
        if row.get("status") == "AUTO_MATCH":
            groups[album_group_key(row)].append(row)

    for rows in groups.values():
        existing = [r.get("local_albumartist", "").strip() for r in rows if r.get("local_albumartist", "").strip()]
        provider = [r.get("matched_albumartist", "").strip() for r in rows if r.get("matched_albumartist", "").strip()]
        if provider:
            chosen = max(set(provider), key=provider.count)
            confidence = "explicit provider albumartist"
        else:
            album_name = rows[0].get("matched_album") or rows[0].get("local_album") or ""
            artists = []
            for r in rows:
                artist = primary_artist(r.get("matched_artist") or r.get("local_artist") or "")
                if artist and norm(artist) not in {norm(x) for x in artists}:
                    artists.append(artist)
            album_key_text = norm(album_name)
            is_soundtrack = any(x in album_key_text for x in ("soundtrack", "original motion picture", " ost", "ost "))
            if is_soundtrack or len(artists) > 1:
                chosen = "Compilation"
                confidence = "multi-artist album grouping"
            elif artists:
                chosen = artists[0]
                confidence = "single consistent track artist"
            else:
                chosen = "Unknown Artist"
                confidence = "no reliable artist metadata"
        for r in rows:
            r["resolved_albumartist"] = chosen
            r["albumartist_basis"] = confidence


def is_standalone_single(row):
    """A verified one-track provider release is treated as a standalone single.

    This avoids misclassifying an incomplete local album as a single: if the
    provider release contains multiple tracks, it remains an album folder even
    when only one of its tracks exists in the input collection.
    """
    detail = str(row.get("detail_status") or "")
    # Support both song-fallback and album-first release descriptions.
    match = re.search(
        r"(?:Verified in\s+|album tracklist\s*\()(\d+)(?:-track release|\s+tracks)",
        detail,
        flags=re.I,
    )
    return bool(match and int(match.group(1)) == 1)


def move_approved(row):
    source = Path(row["source"])
    local = read_local(source)
    track = int(row["track_number"])
    album_name = row.get("matched_album") or local["album"] or "Singles"
    album_artist = row.get("resolved_albumartist") or local.get("albumartist") or primary_artist(local.get("artist", "")) or "Unknown Artist"
    album_key = norm(album_name + " " + row.get("matched_album", ""))
    if is_standalone_single(row):
        dest_dir = OUT / clean_folder(album_artist, "Unknown Artist") / "Singles"
    elif any(token in album_key for token in ("soundtrack", "original motion picture", " ost", "ost ")):
        dest_dir = OUT / "Soundtracks" / clean_folder(album_name, "Unknown Album")
    elif row.get("albumartist_basis") == "multi-artist album grouping":
        dest_dir = OUT / "Compilations" / clean_folder(album_name, "Unknown Album")
    else:
        dest_dir = OUT / clean_folder(album_artist, "Unknown Artist") / clean_folder(album_name, "Singles")
    dest_dir.mkdir(parents=True, exist_ok=True)
    title_for_file = clean_folder(local["title"], source.stem)
    dest = dest_dir / f"{track:02d} - {title_for_file}{source.suffix.lower()}"
    suffix = 2
    while dest.exists():
        dest = dest_dir / f"{track:02d} - {title_for_file} ({suffix}){source.suffix.lower()}"
        suffix += 1
    shutil.move(str(source), str(dest))
    # Local tags take priority. Provider metadata fills only blank/missing fields
    # on the OUTPUT COPY; populated local values are never overwritten.
    values = {
        "title": row.get("matched_title") or local["title"],
        "artist": row.get("matched_artist") or local["artist"],
        "albumartist": local.get("albumartist") or row.get("resolved_albumartist") or row.get("matched_albumartist") or primary_artist(local.get("artist") or row.get("matched_artist")),
        "album": local.get("album") or row.get("matched_album") or album_name,
        "date": local.get("date") or row.get("matched_year"),
        "tracknumber": local.get("tracknumber") or str(track),
        "genre": local.get("genre"),
    }
    try:
        audio = MutagenFile(dest, easy=True)
        if audio is not None:
            if audio.tags is None:
                audio.add_tags()
            for key, value in values.items():
                if not value:
                    continue
                existing = audio.get(key)
                existing_values = existing if isinstance(existing, list) else ([existing] if existing is not None else [])
                has_existing = any(str(item).strip() for item in existing_values)
                if not has_existing:
                    audio[key] = [str(value)]
            audio.save()
    except Exception as exc:
        row["detail_status"] += f"; tag write skipped: {type(exc).__name__}: {exc}"
    for sidecar in source.parent.glob(source.stem + ".*"):
        if sidecar == source or sidecar.suffix.lower() in AUDIO_EXTS:
            continue
        target = dest_dir / sidecar.name
        if not target.exists():
            try:
                shutil.move(str(sidecar), str(target))
            except OSError:
                pass
    row["output"] = str(dest)


def main():
    parser = argparse.ArgumentParser(description="Safely organize local music using verified release tracklists.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Folder containing incoming audio files")
    parser.add_argument("--apply", action="store_true", help="MOVE AUTO_MATCH files to Output and fill only missing metadata tags")
    args = parser.parse_args()
    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Incoming folder not found: {root}")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if not files:
        raise SystemExit("No supported audio files found.")
    DATA.mkdir(parents=True, exist_ok=True)
    if args.apply:
        OUT.mkdir(parents=True, exist_ok=True)

    report, duplicate_hashes = [], {}
    album_groups = defaultdict(list)
    ungrouped = []
    for path in files:
        local = read_local(path)
        album_name = local.get("album") or local.get("folder_album_hint")
        if album_name:
            # Group by source parent and album tag to avoid merging unrelated copies.
            album_groups[(str(path.parent).lower(), norm(album_name))].append(path)
        else:
            ungrouped.append(path)

    processed = 0
    for group_paths in album_groups.values():
        processed += len(group_paths)
        print(f"[{processed}/{len(files)}] Album-first: {group_paths[0].parent.name} ({len(group_paths)} files)")
        try:
            group_rows = process_album_group(group_paths, duplicate_hashes)
            # v13: if album-first could not identify/match a track, try an individual song search.
            # Existing duplicate decisions are retained; fallback is run with a fresh hash map.
            for group_row in group_rows:
                if group_row.get("status") == "REVIEW" and group_row.get("detail_status", "").startswith((
                    "Could not confidently identify the tagged album", "Album identified, but this local track"
                )):
                    fallback_path = Path(group_row["source"])
                    try:
                        fallback_row = process(fallback_path, False, {})
                        fallback_row["match_method"] = "album-first+song-fallback"
                        if fallback_row.get("status") == "AUTO_MATCH":
                            fallback_row["detail_status"] = "Individual song fallback: " + fallback_row.get("detail_status", "")
                        else:
                            fallback_row["detail_status"] = "Album-first: " + group_row.get("detail_status", "") + "; song fallback: " + fallback_row.get("detail_status", "")
                        group_row = fallback_row
                    except Exception as fallback_exc:
                        group_row["detail_status"] += f"; song fallback failed: {type(fallback_exc).__name__}: {fallback_exc}"
                        group_row["match_method"] = "album-first+song-fallback"
                report.append(group_row)
        except Exception as exc:
            for path in group_paths:
                report.append({"source": str(path), "local_title": path.stem, "status": "ERROR",
                               "score": 0, "detail_status": f"Album-first failed: {type(exc).__name__}: {exc}",
                               "match_method": "album-first"})
    for path in ungrouped:
        processed += 1
        print(f"[{processed}/{len(files)}] Single/fallback: {path.name}")
        try:
            row = process(path, False, duplicate_hashes)
            row["match_method"] = "song-fallback"
            report.append(row)
        except Exception as exc:
            report.append({"source": str(path), "local_title": path.stem, "status": "ERROR",
                           "score": 0, "detail_status": f"{type(exc).__name__}: {exc}",
                           "match_method": "song-fallback"})

    infer_album_artists(report)
    if args.apply:
        OUT.mkdir(parents=True, exist_ok=True)
        for row in report:
            if row.get("status") == "AUTO_MATCH":
                try:
                    move_approved(row)
                except Exception as exc:
                    row["status"] = "APPLY_ERROR"
                    row["detail_status"] = f"{row.get('detail_status', '')}; move failed: {type(exc).__name__}: {exc}"

    json_path = DATA / "organizer-v15-report.json"
    csv_path = DATA / "organizer-v15-report.csv"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    columns = sorted({k for r in report for k in r})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(report)

    counts = defaultdict(int)
    for r in report:
        counts[r.get("status", "UNKNOWN")] += 1
    print("\\nCompleted. AUTO_MATCH audio files were moved; review/duplicate files were left in the incoming folder.")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"CSV report: {csv_path}")
    print(f"JSON report: {json_path}")
    if args.apply:
        print(f"Moved output: {OUT}")
    else:
        print("PREVIEW ONLY — no files moved. Review the CSV before using --apply.")


if __name__ == "__main__":
    main()
