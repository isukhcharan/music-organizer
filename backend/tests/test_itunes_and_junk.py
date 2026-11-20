import pytest
from mutagen import File
from mutagen.id3 import ID3, TCON, TXXX

from musicorganizer.cli import parse_providers
from musicorganizer.journal import Journal
from musicorganizer.organizer import apply_plan, plan_moves, undo_job
from musicorganizer.pipeline import match_library
from musicorganizer.providers import ChainProvider, ITunesProvider
from musicorganizer.repair import read_state


class FakeFetch:
    """Stands in for the iTunes HTTP API."""

    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "/lookup" in url:
            return {"results": [
                {"wrapperType": "collection", "collectionName": "AUJLA SZN 1", "artistName": "Karan Aujla",
                 "releaseDate": "2026-09-25T07:00:00Z", "primaryGenreName": "Hip-Hop/Rap", "trackCount": 3},
                {"wrapperType": "track", "kind": "song", "trackName": "Ashke", "artistName": "Karan Aujla", "trackNumber": 1},
                {"wrapperType": "track", "kind": "song", "trackName": "Real Bad Man", "artistName": "Karan Aujla", "trackNumber": 2},
                {"wrapperType": "track", "kind": "song", "trackName": "Straight Up", "artistName": "Karan Aujla", "trackNumber": 3},
            ]}
        if "entity=album" in url:
            return {"results": [{"collectionName": "AUJLA SZN 1", "artistName": "Karan Aujla", "collectionId": 77,
                                 "collectionType": "Album", "collectionViewUrl": "u"}]}
        return {"results": [{"kind": "song", "trackName": "Ashke", "artistName": "Karan Aujla", "trackId": 5,
                             "collectionId": 77, "collectionName": "AUJLA SZN 1", "releaseDate": "2026-09-25T07:00:00Z",
                             "trackViewUrl": "u"}]}


def test_itunes_provider_maps_search_and_album_details():
    f = FakeFetch()
    p = ITunesProvider(fetch=f, min_interval=0)
    song = p.search_songs("Ashke Karan Aujla")[0]
    assert (song["source"], song["title"], song["album_hint"], song["browse_id"], song["year"]) == \
           ("itunes", "Ashke", "AUJLA SZN 1", "77", "2026")
    alb = p.search_albums("AUJLA SZN 1")[0]
    assert alb["browse_id"] == "77" and alb["title"] == "AUJLA SZN 1"
    d = p.album_details(alb)
    assert d["album"] == "AUJLA SZN 1" and d["year"] == "2026" and d["genre"] == "Hip-Hop/Rap"
    assert [(t["sequence"], t["song"]) for t in d["tracks"]] == [("1", "Ashke"), ("2", "Real Bad Man"), ("3", "Straight Up")]
    assert p.album_for_song(song)["album"] == "AUJLA SZN 1"
    assert p.album_for_song({"browse_id": ""}) is None


def test_itunes_end_to_end_gives_real_track_numbers_year_and_genre(make_mp3, tmp_path):
    files = [make_mp3(f"{n}.mp3", folder=tmp_path / "in" / "AUJLA SZN 1", title=n, artist="Karan Aujla", album="AUJLA SZN 1")
             for n in ["Ashke", "Real Bad Man", "Straight Up"]]
    rows = match_library(files, ChainProvider([ITunesProvider(fetch=FakeFetch(), min_interval=0)]), root=tmp_path / "in")
    assert {r["status"] for r in rows} == {"AUTO_MATCH"}
    assert {r["track_number_source"] for r in rows} == {"provider"}      # iTunes states real numbers
    assert rows[0]["matched_genre"] == "Hip-Hop/Rap" and rows[0]["matched_year"] == "2026"
    plan = plan_moves(rows, tmp_path / "out")
    apply_plan(plan, Journal(":memory:"))
    tags = File(plan.ops[0].dest, easy=True)
    assert tags["genre"] == ["Hip Hop"] and tags["date"] == ["2026"]      # blank genre/year filled, spelling mapped


def test_provider_selection():
    assert parse_providers("auto") == ["ytmusic", "jiosaavn", "itunes"]
    assert parse_providers("itunes,ytmusic") == ["ytmusic", "itunes"]       # always in priority order
    assert parse_providers("ITunes") == ["itunes"]
    for bad in ("spotify", "ytmusic,spotify", ","):
        with pytest.raises(ValueError):
            parse_providers(bad)


def junked(path, genre=None, comment=None):
    id3 = ID3(path)
    if genre:
        id3.add(TCON(encoding=3, text=genre))
    if comment:
        id3.add(TXXX(encoding=3, desc="comment", text=comment))
    id3.save(path)
    return path


def row_for(src):
    return {"status": "AUTO_MATCH", "source": str(src), "track_number": "1", "matched_title": "T",
            "matched_artist": "A", "matched_album": "Alb", "matched_year": "2020", "matched_genre": "",
            "detail_status": "Matched against album tracklist (9 tracks)", "resolved_albumartist": "A",
            "albumartist_basis": "single consistent track artist"}


def test_moved_files_arrive_without_junk_and_undo_restores_it_exactly(make_mp3, tmp_path):
    src = junked(make_mp3("t.mp3", folder=tmp_path / "in", title="T", artist="A"),
                 genre="djpunjab", comment="Downloaded From DjPunjab.Com")
    keep = junked(make_mp3("k.mp3", folder=tmp_path / "in", title="K", artist="A"), genre="Bhangra", comment="Live, Toronto 2019")
    j = Journal(tmp_path / "s.db")
    plan = plan_moves([row_for(src), row_for(keep)], tmp_path / "out")
    job = apply_plan(plan, j)
    moved, kept = read_state(plan.ops[0].dest), read_state(plan.ops[1].dest)
    assert moved.genres == [] and moved.comments == []                         # junk gone, nothing invented
    assert kept.genres == ["Bhangra"] and kept.comments == [("TXXX:comment", ["Live, Toronto 2019"])]   # real data kept
    assert src.exists() is False
    undo_job(j, job, stop_at=tmp_path / "out")
    back = read_state(src)
    assert back.genres == ["djpunjab"] and back.comments == [("TXXX:comment", ["Downloaded From DjPunjab.Com"])]
    assert read_state(keep).genres == ["Bhangra"]
    j.close()


def test_provider_genre_fills_a_junk_local_genre_but_never_replaces_a_real_one(make_mp3, tmp_path):
    junk = junked(make_mp3("j.mp3", folder=tmp_path / "in", title="J", artist="A"), genre="latest")
    real = junked(make_mp3("r.mp3", folder=tmp_path / "in", title="R", artist="A"), genre="Bhangra")
    rows = [{**row_for(junk), "matched_genre": "Hip-Hop/Rap"}, {**row_for(real), "matched_genre": "Hip-Hop/Rap"}]
    plan = plan_moves(rows, tmp_path / "out")
    apply_plan(plan, Journal(":memory:"))
    assert read_state(plan.ops[0].dest).genres == ["Hip Hop"]
    assert read_state(plan.ops[1].dest).genres == ["Bhangra"]


def test_itunes_single_and_ep_suffixes_are_not_part_of_the_album_title():
    def fetch(url):
        if "/lookup" in url:
            return {"results": [{"wrapperType": "collection", "collectionName": "Rang Sanwla - Single",
                                 "artistName": "Aarsh Benipal", "releaseDate": "2022-01-01T00:00:00Z"},
                                {"wrapperType": "track", "kind": "song", "trackName": "Rang Sanwla",
                                 "artistName": "Aarsh Benipal", "trackNumber": 1}]}
        if "entity=album" in url:
            return {"results": [{"collectionName": "Moods - EP", "artistName": "X", "collectionId": 1}]}
        return {"results": [{"kind": "song", "trackName": "Rang Sanwla", "artistName": "Aarsh Benipal", "trackId": 1,
                             "collectionId": 9, "collectionName": "Rang Sanwla - Single"}]}

    p = ITunesProvider(fetch=fetch, min_interval=0)
    assert p.search_songs("q")[0]["album_hint"] == "Rang Sanwla"
    assert p.search_albums("q")[0]["title"] == "Moods"
    assert p.album_details({"browse_id": "9"})["album"] == "Rang Sanwla"
    assert p._album("Dr. Zeus - The Album") == "Dr. Zeus - The Album"       # only a trailing Single/EP is removed
