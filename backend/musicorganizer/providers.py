"""Metadata providers.

The matcher only depends on the small ``Provider`` protocol, so it can be tested with fakes and
the real network clients stay optional (imported lazily).

Raw search results are plain dicts; scoring happens in the matcher, not here:

  song result : {source, title, artist, id, url, year, album_hint, browse_id}
  album result: {source, title, artist, id, url, browse_id}
  album detail: {album, album_artist, provider_artist, year,
                 tracks: [{song, primary_artists, sequence}]}
"""
from __future__ import annotations

import html
import logging
import time
from typing import Protocol

log = logging.getLogger(__name__)


class Provider(Protocol):
    name: str

    def search_songs(self, query: str) -> list[dict]: ...

    def search_albums(self, query: str) -> list[dict]: ...

    def album_details(self, ref: dict) -> dict | None:
        """Full release (tracklist) for an album search result."""

    def album_for_song(self, candidate: dict) -> dict | None:
        """Release a song search result belongs to, if it can be resolved."""


class ChainProvider:
    """YouTube Music first; later providers are used only when earlier ones return nothing.

    Mirrors the legacy policy: JioSaavn is a last-resort fallback, never a tie-breaker.
    """

    name = "chain"

    def __init__(self, providers: list[Provider]):
        self.providers = providers
        self._by_name = {p.name: p for p in providers}

    def _first_nonempty(self, method: str, query: str) -> list[dict]:
        for provider in self.providers:
            try:
                results = getattr(provider, method)(query)
            except Exception as exc:  # network/provider failure must not abort a scan
                log.warning("%s.%s failed: %s: %s", provider.name, method, type(exc).__name__, exc)
                continue
            if results:
                return results
        return []

    def search_songs(self, query):
        return self._first_nonempty("search_songs", query)

    def search_albums(self, query):
        return self._first_nonempty("search_albums", query)

    def album_details(self, ref):
        provider = self._by_name.get(ref.get("source", ""))
        return provider.album_details(ref) if provider else None

    def album_for_song(self, candidate):
        provider = self._by_name.get(candidate.get("source", ""))
        return provider.album_for_song(candidate) if provider else None


def _artists(items) -> str:
    return ", ".join(a.get("name", "") for a in (items or []) if a.get("name"))


class YTMusicProvider:
    name = "ytmusic"

    def __init__(self, client=None):
        if client is None:
            from ytmusicapi import YTMusic  # lazy: optional dependency

            client = YTMusic()
        self.yt = client

    def search_songs(self, query):
        out = []
        for item in self.yt.search(query, filter="songs")[:10]:
            album = item.get("album") or {}
            out.append({
                "source": self.name, "title": item.get("title", ""), "artist": _artists(item.get("artists")),
                "id": item.get("videoId", ""), "url": "", "year": "",
                "album_hint": album.get("name", "") if isinstance(album, dict) else "",
                "browse_id": album.get("id", "") if isinstance(album, dict) else "",
            })
        return out

    def search_albums(self, query):
        out = []
        for item in self.yt.search(query, filter="albums")[:10]:
            title, bid = item.get("title", ""), item.get("browseId", "")
            if title and bid:
                out.append({"source": self.name, "title": title, "artist": _artists(item.get("artists")),
                            "id": bid, "url": "", "browse_id": bid})
        return out

    def album_details(self, ref):
        bid = ref.get("browse_id")
        if not bid:
            return None
        album = self.yt.get_album(bid)
        tracks = []
        for idx, t in enumerate(album.get("tracks", []), 1):
            tracks.append({"song": t.get("title", ""), "primary_artists": _artists(t.get("artists")),
                           "sequence": str(t.get("trackNumber") or idx), "duration": t.get("duration", "")})
        return {"album": album.get("title") or ref.get("title", ""), "album_artist": "",
                "provider_artist": _artists(album.get("artists")), "year": str(album.get("year") or ""),
                "tracks": tracks}

    def album_for_song(self, candidate):
        return self.album_details(candidate) if candidate.get("browse_id") else None


class SaavnProvider:
    """JioSaavn via musicapy. Ported from the legacy script; response shapes vary, so it is defensive."""

    name = "jiosaavn"
    PAUSE = 0.20

    def __init__(self, client=None):
        if client is None:
            from musicapy.saavn_api.api import SaavnAPI  # lazy: optional dependency

            client = SaavnAPI()
        self.api = client

    # -- payload helpers (legacy list_payload / first / unwrap_album) ------------------------------
    @staticmethod
    def _scalar(v):
        return str(v).strip() if isinstance(v, (str, int, float)) else ""

    def _first(self, d, *keys):
        if not isinstance(d, dict):
            return ""
        for k in keys:
            v = self._scalar(d.get(k))
            if v:
                return html.unescape(v)
        return ""

    def _list_payload(self, value):
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for key in ("data", "results", "songs", "albums", "items"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
                if isinstance(nested, dict):
                    found = self._list_payload(nested)
                    if found:
                        return found
        return []

    @staticmethod
    def _artist_core(value):
        return html.unescape(str(value or "")).strip().split(" - ", 1)[0].strip()

    @staticmethod
    def _unwrap_album(value):
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

    def _tracklist(self, album):
        for key in ("songs", "tracks", "tracklist", "items"):
            value = album.get(key) if isinstance(album, dict) else None
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []

    # -- Provider API -----------------------------------------------------------------------------
    def search_songs(self, query):
        raw = self.api.search_song(query)
        time.sleep(self.PAUSE)
        out = []
        for item in self._list_payload(raw):
            title = self._first(item, "song", "title", "name")
            if not title:
                continue
            artist_raw = self._first(item, "primary_artists", "artist", "subtitle")
            out.append({"source": self.name, "title": title, "artist": self._artist_core(artist_raw),
                        "id": self._first(item, "id", "songid", "song_id"),
                        "url": self._first(item, "perma_url", "url", "song_url"),
                        "year": self._first(item, "year", "release_year"),
                        "album_hint": self._first(item, "album", "album_name"), "browse_id": ""})
        return out

    def search_albums(self, query):
        raw = self.api.search_album(query)
        time.sleep(self.PAUSE)
        out = []
        for item in self._list_payload(raw):
            title = self._first(item, "title", "name", "album")
            artist = self._first(item, "primary_artists", "artist", "subtitle")
            url = self._first(item, "perma_url", "url", "album_url")
            aid = self._first(item, "id", "albumid", "album_id")
            if title and (url or aid):
                out.append({"source": self.name, "title": title, "artist": self._artist_core(artist),
                            "url": url, "id": aid, "browse_id": ""})
        return out

    def _album_identifier(self, song):
        url = self._first(song, "album_url", "perma_url_album", "album_link")
        aid = self._first(song, "albumid", "album_id")
        album_obj = song.get("album")
        if isinstance(album_obj, dict):
            url = url or self._first(album_obj, "url", "perma_url")
            aid = aid or self._first(album_obj, "id", "albumid", "album_id")
        if url:
            return self.api.create_identifier(url, "album")
        if aid:
            return self.api.create_identifier(aid, "album")
        return None

    def album_details(self, ref):
        ident = (self.api.create_identifier(ref["url"], "album") if ref.get("url")
                 else self.api.create_identifier(ref["id"], "album"))
        if ident is None:
            return None
        album = self._unwrap_album(self.api.get_album_details(ident))
        time.sleep(self.PAUSE)
        if not album:
            return None
        return {"album": self._first(album, "title", "name") or ref.get("title", ""),
                "album_artist": self._first(album, "album_artist", "albumartist"),
                "provider_artist": self._first(album, "primary_artists", "artist") or ref.get("artist", ""),
                "year": self._first(album, "year", "release_year"), "tracks": self._tracklist(album)}

    def album_for_song(self, candidate):
        ident = None
        if candidate.get("url"):
            ident = self.api.create_identifier(candidate["url"], "song")
        elif candidate.get("id"):
            ident = self.api.create_identifier(candidate["id"], "song")
        if ident is None:
            return None
        song = self.api.get_song_details(ident)
        time.sleep(self.PAUSE)
        if isinstance(song, list):
            song = song[0] if song and isinstance(song[0], dict) else {}
        if not isinstance(song, dict):
            return None
        if candidate.get("id") in song and isinstance(song[candidate["id"]], dict):
            song = song[candidate["id"]]
        if isinstance(song.get("data"), dict):
            song = song["data"]
        if not song:
            return None
        album_ident = self._album_identifier(song)
        album = {}
        if album_ident is not None:
            try:
                album = self._unwrap_album(self.api.get_album_details(album_ident))
                time.sleep(self.PAUSE)
            except Exception:
                album = {}
        return {
            "album": self._first(album, "title", "name") or self._first(song, "album") or candidate.get("album_hint", ""),
            "album_artist": self._first(album, "album_artist", "albumartist"),
            "provider_artist": "",
            "year": self._first(album, "year", "release_year") or self._first(song, "year", "release_year")
                    or candidate.get("year", ""),
            "tracks": self._tracklist(album),
        }


class ITunesProvider:
    """Apple iTunes Search API (free, no key). Gives real track numbers, release year and a genre.

    Rate limited by Apple (~20 requests/minute), so requests are spaced out and one retry is made after a
    429/403. ``fetch(url) -> dict`` is injectable for tests.
    """

    name = "itunes"
    BASE = "https://itunes.apple.com"

    def __init__(self, fetch=None, min_interval: float = 1.5, country: str = "US"):
        self._fetch = fetch or self._http
        self.min_interval = min_interval
        self.country = country
        self._last = 0.0

    def _http(self, url: str) -> dict:
        import json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "music-organizer/0.1"})
        for attempt in (1, 2):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                return json.load(urllib.request.urlopen(req, timeout=20))
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429) and attempt == 1:
                    time.sleep(30)          # rate limited: back off once, then try again
                    continue
                raise
        return {}

    def _get(self, path: str, **params) -> list[dict]:
        from urllib.parse import urlencode

        url = f"{self.BASE}/{path}?" + urlencode({**params, "country": self.country})
        return list(self._fetch(url).get("results", []))

    @staticmethod
    def _year(date: str) -> str:
        return (date or "")[:4]

    @staticmethod
    def _album(name: str) -> str:
        """iTunes names singles 'Title - Single' and EPs 'Title - EP'; that suffix is not part of the album title."""
        import re

        return re.sub(r"\s+-\s+(single|ep)\s*$", "", name or "", flags=re.I).strip()

    def search_songs(self, query):
        out = []
        for r in self._get("search", term=query, entity="song", limit=10):
            if r.get("kind") != "song":
                continue
            out.append({"source": self.name, "title": r.get("trackName", ""), "artist": r.get("artistName", ""),
                        "id": str(r.get("trackId", "")), "url": r.get("trackViewUrl", ""),
                        "year": self._year(r.get("releaseDate", "")), "album_hint": self._album(r.get("collectionName", "")),
                        "browse_id": str(r.get("collectionId", "") or "")})
        return out

    def search_albums(self, query):
        out = []
        for r in self._get("search", term=query, entity="album", limit=10):
            if r.get("collectionType") not in (None, "Album") or not r.get("collectionName"):
                continue
            cid = str(r.get("collectionId", ""))
            out.append({"source": self.name, "title": self._album(r["collectionName"]), "artist": r.get("artistName", ""),
                        "id": cid, "url": r.get("collectionViewUrl", ""), "browse_id": cid})
        return out

    def album_details(self, ref):
        cid = ref.get("browse_id") or ref.get("id")
        if not cid:
            return None
        rows = self._get("lookup", id=cid, entity="song")
        album = next((r for r in rows if r.get("wrapperType") == "collection"), None)
        if not album:
            return None
        tracks = [{"song": t.get("trackName", ""), "primary_artists": t.get("artistName", ""),
                   "sequence": str(t.get("trackNumber") or "")}
                  for t in rows if t.get("wrapperType") == "track" and t.get("kind") == "song"]
        return {"album": self._album(album.get("collectionName", "")), "album_artist": "",
                "provider_artist": album.get("artistName", ""), "year": self._year(album.get("releaseDate", "")),
                "genre": album.get("primaryGenreName", ""), "tracks": tracks}

    def album_for_song(self, candidate):
        return self.album_details(candidate) if candidate.get("browse_id") else None
