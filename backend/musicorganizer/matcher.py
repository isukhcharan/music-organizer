"""Matching local files to provider releases.

Ported from legacy/music_organizer_v15_move.py with the network removed: every function takes a
``Provider`` (see providers.py). Thresholds are unchanged. Rows are dicts with the legacy report keys.

Statuses: AUTO_MATCH, REVIEW, REVIEW_RELEASE_CONFLICT, REVIEW_DUPLICATE, NO_RESULT.
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

from .duplicates import DuplicateTracker
from .providers import Provider
from .text import (
    album_identity_score,
    artist_match_score,
    norm,
    sim,
    tracknumber_value,
)

MIN_GAP = 0.045
log = logging.getLogger(__name__)

YT_UNVERIFIED = "YouTube Music result found, but track could not be verified in its album tracklist"
ALBUM_FIRST_FAILED_PREFIXES = (
    "Could not confidently identify the tagged album",
    "Album identified, but this local track",
)


# -- small payload helpers ------------------------------------------------------------------------
def _scalar(v) -> str:
    return str(v).strip() if isinstance(v, (str, int, float)) else ""


def _first(d, *keys) -> str:
    if not isinstance(d, dict):
        return ""
    for k in keys:
        v = _scalar(d.get(k))
        if v:
            return html.unescape(v)
    return ""


def _track_title(t) -> str:
    return _first(t, "song", "title", "name")


def _track_artist(t) -> str:
    return _first(t, "primary_artists", "singers", "artist")


def _track_number(t, fallback: int) -> int:
    explicit = _first(t, "sequence", "track_number", "tracknumber", "position")
    return int(explicit) if explicit.isdigit() and int(explicit) > 0 else fallback


def _has_explicit_number(t) -> bool:
    """True when the provider itself states the track's position (not just its place in a list)."""
    explicit = _first(t, "sequence", "track_number", "tracknumber", "position")
    return explicit.isdigit() and int(explicit) > 0


def choose_track_number(local: dict, provider_number: int, reliable: bool) -> tuple[str, str]:
    """Track number for the output *filename*, and where it came from.

    A provider list position is NOT evidence of album order (JioSaavn lists tracks in a different order
    and has no number field). So: a real provider number wins; otherwise the file's own tag; otherwise none.
    """
    if reliable:
        return str(provider_number), "provider"
    own = tracknumber_value(local.get("tracknumber"))
    if own:
        return str(own), "local"
    return "", "none"


def new_row(path: Path, local: dict, candidates: list[dict], method: str = "") -> dict:
    row = {
        "source": str(path), "local_title": local["title"], "local_artist": local["artist"],
        "local_albumartist": local["albumartist"], "local_album": local["album"],
        "folder_album_hint": local["folder_album_hint"], "status": "REVIEW",
        "score": 0, "matched_title": "", "matched_artist": "", "matched_album": "",
        "matched_albumartist": "", "matched_year": "", "provider_id": "", "provider_url": "",
        "track_number": "", "release_track_match": "", "duplicate_of": "", "output": "",
        "detail_status": "", "candidates": json.dumps(candidates[:8], ensure_ascii=False),
    }
    if method:
        row["match_method"] = method
    return row


# -- track-level matching (pure) ------------------------------------------------------------------
def verify_track(local_title: str, tracks: list[dict]) -> dict | None:
    """Find the local title in a tracklist; only a clear, unique winner counts."""
    ranked = []
    for idx, track in enumerate(tracks, 1):
        title = _track_title(track)
        if title:
            ranked.append((sim(local_title, title), idx, title, track))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0
    if best[0] >= 0.90 and best[0] - runner >= 0.035:
        return {"score": round(best[0], 4), "number": str(_track_number(best[3], best[1])), "title": best[2],
                "reliable": _has_explicit_number(best[3])}
    return None


def match_local_to_track(local: dict, tracks: list[dict], used_positions: set) -> dict | None:
    """Match one local file to one unique provider track; never assigns a position twice."""
    ranked = []
    for idx, track in enumerate(tracks, 1):
        number = _track_number(track, idx)
        if number in used_positions:
            continue
        title = _track_title(track)
        if not title:
            continue
        title_score = sim(local["title"], title)
        local_artist = local.get("artist", "")
        remote_artist = _track_artist(track)
        artist_score = artist_match_score(local_artist, remote_artist) if local_artist and remote_artist else 0.0
        score = 0.72 * title_score + 0.28 * artist_score
        ranked.append((score, title_score, number, title, track))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0
    remote_artist = _track_artist(best[4])
    artist_ok = (not local.get("artist") or not remote_artist
                 or artist_match_score(local.get("artist"), remote_artist) >= 0.62)
    if best[1] >= 0.84 and artist_ok and (best[0] - runner >= 0.025 or best[1] >= 0.98):
        return {"score": round(best[0], 4), "title_score": round(best[1], 4),
                "number": best[2], "title": best[3], "artist": remote_artist,
                "reliable": _has_explicit_number(best[4])}
    return None


# -- song search ----------------------------------------------------------------------------------
def search_song_candidates(provider: Provider, local: dict, path: Path) -> list[dict]:
    title = local["title"] or path.stem
    artist = local["artist"] or local["albumartist"]
    query = " ".join(x for x in (title, artist) if x).strip()
    unique: dict = {}
    for item in provider.search_songs(query):
        ts = sim(title, item["title"])
        ars = artist_match_score(artist, item["artist"]) if artist else 0.0
        cand = dict(item, score=round(0.70 * ts + 0.30 * ars, 4),
                    title_score=round(ts, 4), artist_score=round(ars, 4), artist_raw=item["artist"])
        unique.setdefault(item.get("id") or (item["title"], item["artist"], item.get("album_hint")), cand)
    return sorted(unique.values(), key=lambda c: c["score"], reverse=True)


def resolve_song_candidate(provider: Provider, candidate: dict, local: dict) -> tuple[dict | None, str]:
    detail = provider.album_for_song(candidate)
    ytmusic = candidate.get("source") == "ytmusic"
    if detail is None:
        if not ytmusic:
            return None, "Song details unavailable"
        detail = {"album": candidate.get("album_hint", ""), "album_artist": "", "year": "", "tracks": []}
    tracks = detail.get("tracks", [])
    verified = verify_track(local["title"], tracks) if tracks else None
    error = "" if verified else (YT_UNVERIFIED if ytmusic else "Tracklist verification unavailable")
    return {"album": detail.get("album") or candidate.get("album_hint", ""),
            "album_artist": detail.get("album_artist", ""), "year": detail.get("year", "") or candidate.get("year", ""),
            "tracks": tracks, "verified": verified}, error


def identify_single(path: Path, local: dict, provider: Provider, duplicates: DuplicateTracker) -> dict:
    """Song-level identification (used for untagged files and as the album-first fallback)."""
    candidates = search_song_candidates(provider, local, path)
    row = new_row(path, local, candidates)
    if not candidates:
        row["status"], row["detail_status"] = "NO_RESULT", "No search result"
        return row

    original = duplicates.check(path)
    if original:
        row.update(status="REVIEW_DUPLICATE", duplicate_of=original,
                   detail_status="Byte-identical copy of another input file; keep one copy")
        return row

    if candidates[0]["title_score"] < 0.90:
        row["detail_status"] = "Top search title is not a close enough match"
        return row

    local_artist = local.get("artist") or local.get("albumartist")
    artist_checked = [c for c in candidates
                      if not local_artist or not c.get("artist") or artist_match_score(local_artist, c["artist"]) >= 0.62]
    if not artist_checked:
        row["detail_status"] = ("Song title matched, but provider artist credit does not agree with "
                                "local artist; manual review required")
        return row

    top = artist_checked[0]["score"]
    pool = [c for c in artist_checked[:4] if c["title_score"] >= 0.90 and c["score"] >= top - 0.12]
    resolved = []
    for candidate in pool:
        try:
            detail, error = resolve_song_candidate(provider, candidate, local)
        except Exception as exc:
            log.warning("could not resolve %r (%s): %s: %s", candidate.get("title"),
                        candidate.get("source"), type(exc).__name__, exc)
            continue
        if detail:
            album_hint = local["album"] or local["folder_album_hint"]
            detail["album_similarity"] = sim(album_hint, detail["album"]) if album_hint and detail["album"] else 0
            detail["candidate"], detail["error"] = candidate, error
            resolved.append(detail)

    if not resolved:
        row["detail_status"] = ("No candidate has both a close title and a matching artist"
                                if not pool else "Could not resolve song/release details")
        return row

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
        resolved = sorted(compatible, key=lambda r: (r["album_similarity"], r["candidate"]["score"]), reverse=True)
    else:
        resolved.sort(key=lambda r: r["candidate"]["score"], reverse=True)

    chosen = resolved[0]
    cand = chosen["candidate"]
    if len(resolved) > 1:
        second = resolved[1]
        if (abs(cand["score"] - second["candidate"]["score"]) < MIN_GAP
                and abs(chosen["album_similarity"] - second["album_similarity"]) < 0.08):
            row["detail_status"] = "Multiple equally plausible releases; needs a manual choice"
            return row

    row.update({"score": cand["score"], "matched_title": cand["title"], "matched_artist": cand["artist"],
                "matched_album": chosen["album"], "matched_albumartist": chosen["album_artist"],
                "matched_year": chosen["year"], "provider_id": cand["id"], "provider_url": cand["url"]})
    verified = chosen.get("verified")
    if not verified:
        row["detail_status"] = chosen["error"] or "Could not uniquely verify title in release tracklist"
        return row

    track, source = choose_track_number(local, int(verified["number"]), verified["reliable"])
    existing = tracknumber_value(local["tracknumber"])
    detail = f"Verified in {len(chosen['tracks'])}-track release; provider artist and title checked"
    if source == "provider" and existing and existing != int(track):
        detail += (f". Local track-number tag {existing} will be preserved; provider album position "
                   f"{track} will be used for output filename ordering.")
    row.update(status="AUTO_MATCH", track_number=track, track_number_source=source,
               release_tracks=len(chosen["tracks"]),
               release_track_match=verified["title"], detail_status=detail)
    return row


# -- album-first ----------------------------------------------------------------------------------
def _album_queries(album_name: str, artist_name: str) -> list[str]:
    """Most specific query first; looser ones only run if the earlier ones return nothing.

    YouTube Music returns nothing for some tag spellings (e.g. 'Vol-1.0'), so the last rung is the
    album title reduced to plain words.
    """
    queries = [" ".join(x for x in (album_name, artist_name) if x).strip(), album_name.strip(), norm(album_name)]
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def search_album_candidates(provider: Provider, album_name: str, artist_name: str,
                            artist_pool: str = "") -> list[dict]:
    """``artist_pool`` is every local track artist, so multi-artist albums are not judged on one guess."""
    results: list[dict] = []
    for query in _album_queries(album_name, artist_name):
        results = provider.search_albums(query)
        if results:
            break
    found = []
    for item in results:
        artist_score = sim(artist_name, item["artist"]) if artist_name and item["artist"] else 0.5
        if artist_pool and item["artist"]:
            artist_score = max(artist_score, artist_match_score(artist_pool, item["artist"]))
        found.append(dict(
            item, artist_raw=item["artist"], title_score=sim(album_name, item["title"]),
            identity_score=album_identity_score(album_name, item["title"]), artist_score=artist_score))
    return sorted(found, key=lambda c: (c["identity_score"], c["artist_score"]), reverse=True)


def _most_common(values: list[str]) -> str:
    return max(set(values), key=values.count) if values else ""


def identify_album_group(paths: list[Path], locals_by_path: dict, provider: Provider,
                         duplicates: DuplicateTracker) -> list[dict]:
    """Resolve one tagged album as a unit, then match each local file to its tracklist."""
    first_local = next(iter(locals_by_path.values()))
    album_name = first_local.get("album") or first_local.get("folder_album_hint") or ""
    artist_hint = (_most_common([v["albumartist"] for v in locals_by_path.values() if v.get("albumartist")])
                   or _most_common([v["artist"] for v in locals_by_path.values() if v.get("artist")]))

    artist_pool = " / ".join(dict.fromkeys(v["artist"] for v in locals_by_path.values() if v.get("artist")))
    candidates = search_album_candidates(provider, album_name, artist_hint, artist_pool)
    candidates = [c for c in candidates
                  if c["identity_score"] >= 0.82 and (c["artist_score"] >= 0.35 or not artist_hint)]
    resolved = []
    for candidate in candidates[:5]:
        detail = provider.album_details(candidate)
        if detail and detail.get("tracks"):
            detail["candidate"] = candidate
            resolved.append(detail)

    evaluated = []
    for detail in resolved:
        used: set = set()
        matches: dict = {}
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
        row = new_row(path, local, candidates, "album-first")
        original = duplicates.check(path)
        if original:
            row.update(status="REVIEW_DUPLICATE", duplicate_of=original,
                       detail_status="Byte-identical copy of another input file; keep one copy")
            rows.append(row)
            continue
        if not chosen:
            row["detail_status"] = "Could not confidently identify the tagged album and its tracklist"
            rows.append(row)
            continue

        _, coverage, detail, matches = chosen
        cand = detail["candidate"]
        match = matches.get(path)
        row.update({"score": round(chosen[0], 4), "matched_album": detail["album"],
                    "matched_albumartist": detail.get("album_artist", ""),
                    "matched_artist": detail.get("provider_artist", ""), "matched_year": detail.get("year", ""),
                    "provider_id": cand.get("id", ""), "provider_url": cand.get("url", "")})
        if not match:
            row["detail_status"] = "Album identified, but this local track did not uniquely match a track on its tracklist"
            rows.append(row)
            continue
        track, source = choose_track_number(local, match["number"], match["reliable"])
        existing = tracknumber_value(local.get("tracknumber"))
        note = (f" Local track-number tag {existing} will be preserved; provider album position "
                f"{track} will be used for output filename ordering."
                if source == "provider" and existing and existing != int(track) else "")
        row.update(status="AUTO_MATCH", release_tracks=len(detail["tracks"]), matched_title=match["title"],
                   matched_artist=match.get("artist") or detail.get("provider_artist", ""),
                   track_number=track, track_number_source=source, release_track_match=match["title"],
                   detail_status=f"Matched against album tracklist ({len(detail['tracks'])} tracks); "
                                 f"album coverage {coverage:.0%}.{note}")
        rows.append(row)
    return rows
