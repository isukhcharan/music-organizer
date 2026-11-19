import shutil

import pytest

from musicorganizer.pipeline import match_library
from musicorganizer.providers import ChainProvider


class FakeProvider:
    """Deterministic provider: albums keyed by browse_id, searches keyed by query text."""

    name = "ytmusic"

    def __init__(self, albums=None, song_results=None, album_results=None):
        self.albums = albums or {}
        self.song_results = song_results or {}
        self.album_results = album_results or {}
        self.calls = []

    def search_songs(self, query):
        self.calls.append(("songs", query))
        return self.song_results.get(query, [])

    def search_albums(self, query):
        self.calls.append(("albums", query))
        return self.album_results.get(query, [])

    def album_details(self, ref):
        return self.albums.get(ref.get("browse_id"))

    def album_for_song(self, candidate):
        return self.albums.get(candidate.get("browse_id")) if candidate.get("browse_id") else None


def album(title, tracks, artist="Sidhu Moose Wala", year="2018"):
    return {"album": title, "album_artist": "", "provider_artist": artist, "year": year,
            "tracks": [{"song": t, "primary_artists": artist, "sequence": str(i)} for i, t in enumerate(tracks, 1)]}


def album_hit(title, bid, artist="Sidhu Moose Wala"):
    return {"source": "ytmusic", "title": title, "artist": artist, "id": bid, "url": "", "browse_id": bid}


def song_hit(title, artist, album_name, bid, vid="v1"):
    return {"source": "ytmusic", "title": title, "artist": artist, "id": vid, "url": "", "year": "",
            "album_hint": album_name, "browse_id": bid}


def by_title(rows):
    return {r["local_title"]: r for r in rows}


def test_album_first_matches_whole_album_and_uses_provider_order(make_mp3, tmp_path):
    tracks = ["295", "Dollar", "Chorni", "Burberry"]
    files = [make_mp3(f"{n}.mp3", folder=tmp_path / "Moosetape", title=n, artist="Sidhu Moose Wala",
                      album="Moosetape") for n in tracks]
    prov = FakeProvider(albums={"B1": album("Moosetape", tracks)},
                        album_results={"Moosetape Sidhu Moose Wala": [album_hit("Moosetape", "B1")]})
    rows = by_title(match_library(files, prov))
    assert {r["status"] for r in rows.values()} == {"AUTO_MATCH"}
    assert [rows[t]["track_number"] for t in tracks] == ["1", "2", "3", "4"]
    assert all(r["match_method"] == "album-first" for r in rows.values())
    assert rows["295"]["resolved_albumartist"] == "Sidhu Moose Wala"


def test_lone_track_from_big_album_is_matched_but_not_a_single(make_mp3, tmp_path):
    names = [f"Track {c}" for c in "ABCDEFGHI"]
    f = make_mp3("Track B.mp3", folder=tmp_path / "8 Chances", title="Track B", artist="Harnoor", album="8 Chances")
    prov = FakeProvider(albums={"B1": album("8 Chances", names, "Harnoor")},
                        album_results={"8 Chances Harnoor": [album_hit("8 Chances", "B1", "Harnoor")]})
    [row] = match_library([f], prov)
    assert row["status"] == "AUTO_MATCH" and row["track_number"] == "2"
    assert "(9 tracks)" in row["detail_status"]


def test_untagged_file_uses_song_search_and_verifies_in_release(make_mp3, tmp_path):
    f = make_mp3("Photo.mp3", folder=tmp_path / "Incoming", title="Photo", artist="Singga")
    prov = FakeProvider(
        albums={"B9": album("Photo", ["Photo"], "Singga")},
        song_results={"Photo Singga": [song_hit("Photo", "Singga", "Photo", "B9")]})
    [row] = match_library([f], prov)
    assert row["status"] == "AUTO_MATCH"
    assert row["detail_status"].startswith("Verified in 1-track release")
    assert row["match_method"] == "song-fallback"


def test_release_conflict_when_local_album_disagrees(make_mp3, tmp_path):
    f = make_mp3("Haan Karde.mp3", folder=tmp_path / "Incoming", title="Haan Karde", artist="Someone",
                 album="My Own Mix")
    # No album-first hit, so it falls back to song search, which finds a different release.
    prov = FakeProvider(
        albums={"B2": album("Haan Karde", ["Haan Karde"], "Someone")},
        song_results={"Haan Karde Someone": [song_hit("Haan Karde", "Someone", "Haan Karde", "B2")]})
    [row] = match_library([f], prov)
    assert row["status"] == "REVIEW_RELEASE_CONFLICT"
    assert "My Own Mix" in row["detail_status"]
    assert row["match_method"] == "album-first+song-fallback"


def test_ambiguous_releases_stay_in_review(make_mp3, tmp_path):
    f = make_mp3("Supne.mp3", folder=tmp_path / "Incoming", title="Supne", artist="Artist")
    prov = FakeProvider(
        albums={"A": album("Supne", ["Supne", "x"], "Artist"), "B": album("Supne (Deluxe)", ["Supne", "y"], "Artist")},
        song_results={"Supne Artist": [song_hit("Supne", "Artist", "Supne", "A", "v1"),
                                       song_hit("Supne", "Artist", "Supne (Deluxe)", "B", "v2")]})
    [row] = match_library([f], prov)
    assert row["status"] == "REVIEW"
    assert "equally plausible" in row["detail_status"]


def test_no_result(make_mp3, tmp_path):
    f = make_mp3("Zzz.mp3", folder=tmp_path / "Incoming", title="Zzz", artist="Nobody")
    [row] = match_library([f], FakeProvider())
    assert row["status"] == "NO_RESULT"


def test_title_not_close_enough_goes_to_review(make_mp3, tmp_path):
    f = make_mp3("Lethal Combination.mp3", folder=tmp_path / "Incoming", title="Lethal Combination", artist="A")
    prov = FakeProvider(song_results={"Lethal Combination A": [song_hit("Totally Different", "A", "X", "B1")]})
    [row] = match_library([f], prov)
    assert row["status"] == "REVIEW" and "not a close enough match" in row["detail_status"]


def test_byte_identical_duplicate_is_flagged_never_removed(make_mp3, tmp_path):
    inc = tmp_path / "Incoming"
    a = make_mp3("Photo.mp3", folder=inc, title="Photo", artist="Singga")
    b = inc / "Photo copy.mp3"
    shutil.copy(a, b)
    prov = FakeProvider(
        albums={"B9": album("Photo", ["Photo"], "Singga")},
        song_results={"Photo Singga": [song_hit("Photo", "Singga", "Photo", "B9")]})
    rows = match_library([a, b], prov)
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["AUTO_MATCH", "REVIEW_DUPLICATE"]
    dup = next(r for r in rows if r["status"] == "REVIEW_DUPLICATE")
    assert dup["duplicate_of"] == str(a)
    assert a.exists() and b.exists()


def test_local_track_number_conflict_is_noted_and_provider_position_used(make_mp3, tmp_path):
    names = ["One", "Two", "Three"]
    f = make_mp3("Two.mp3", folder=tmp_path / "Alb", title="Two", artist="Art", album="Alb", tracknumber="7")
    prov = FakeProvider(albums={"B": album("Alb", names, "Art")},
                        album_results={"Alb Art": [album_hit("Alb", "B", "Art")]})
    [row] = match_library([f], prov)
    assert row["track_number"] == "2"
    assert "Local track-number tag 7 will be preserved" in row["detail_status"]


def test_provider_exception_becomes_error_row_not_a_crash(make_mp3, tmp_path):
    class Boom(FakeProvider):
        def search_songs(self, q):
            raise RuntimeError("network down")

    f = make_mp3("A.mp3", folder=tmp_path / "Incoming", title="A", artist="B")
    [row] = match_library([f], Boom())
    assert row["status"] == "ERROR" and "network down" in row["detail_status"]


def test_chain_falls_back_to_second_provider_only_when_first_is_empty_or_fails():
    class Saavn(FakeProvider):
        name = "jiosaavn"

    yt_down = FakeProvider()
    yt_down.search_songs = lambda q: (_ for _ in ()).throw(RuntimeError("boom"))
    saavn = Saavn(song_results={"q": [{"source": "jiosaavn", "title": "t"}]})
    assert ChainProvider([yt_down, saavn]).search_songs("q")[0]["title"] == "t"

    yt = FakeProvider(song_results={"q": [{"source": "ytmusic", "title": "yt"}]})
    assert ChainProvider([yt, saavn]).search_songs("q")[0]["title"] == "yt"


def test_progress_callback_reports_every_file(make_mp3, tmp_path):
    files = [make_mp3(f"{i}.mp3", folder=tmp_path / "Incoming", title=f"T{i}", artist="A") for i in range(3)]
    seen = []
    match_library(files, FakeProvider(), progress=lambda done, total, label: seen.append((done, total)))
    assert seen[-1] == (3, 3)


def test_norm_collapses_whitespace_so_punctuation_spelling_does_not_hurt_similarity():
    from musicorganizer.text import norm, sim
    assert norm("PANJ-AAB, Vol. 1") == "panj aab vol 1"
    assert sim("Panj-Aab Vol-1.0", "PANJ-AAB, Vol. 1") >= 0.88


def test_multi_artist_album_with_awkward_tag_is_found_by_album_first(make_mp3, tmp_path):
    """Real case: tag 'Panj-Aab Vol-1.0' finds nothing on YT Music; the provider artist is just one of many."""
    tracks = {"Goriye": "Amrit Maan/Harjesh Bittu", "Proposal": "Mehtab Virk/Muzical Doctorz", "Pendu": "Nit Mann"}
    files = [make_mp3(f"{t}.mp3", folder=tmp_path / "Album - Panj-Aab Vol-1.0", title=t, artist=a,
                      album="Panj-Aab Vol-1.0") for t, a in tracks.items()]
    real = album("PANJ-AAB, Vol. 1", ["Goriye", "Proposal", "Pendu", "Other"], "Muzical Doctorz")
    for trk, who in zip(real["tracks"], ["Amrit Maan", "Mehtab Virk", "Nit Mann", "Someone Else"]):
        trk["primary_artists"] = who      # compilation: each track credited to its own artist

    class Picky(FakeProvider):
        def search_albums(self, query):          # like YouTube Music: odd spellings return nothing
            self.calls.append(("albums", query))
            return [album_hit("PANJ-AAB, Vol. 1", "B1", "Muzical Doctorz")] if query == "panj aab vol 1 0" else []

    prov = Picky(albums={"B1": real})
    rows = match_library(files, prov)
    assert {r["status"] for r in rows} == {"AUTO_MATCH"}
    assert all(r["match_method"] == "album-first" for r in rows)
    assert [q for kind, q in prov.calls if kind == "albums"][-1] == "panj aab vol 1 0"   # ladder reached last rung


def test_album_search_stops_at_first_rung_that_returns_results():
    prov = FakeProvider(album_results={"Alb Art": [album_hit("Alb", "B")]})
    from musicorganizer.matcher import search_album_candidates
    assert len(search_album_candidates(prov, "Alb", "Art")) == 1
    assert prov.calls == [("albums", "Alb Art")]


def test_feat_credit_in_title_does_not_hurt_similarity():
    from musicorganizer.text import norm, sim
    assert norm("Lamborghini (feat. Shipra Goyal)") == "lamborghini"
    assert norm("Surma [ft. Raj Shoker]") == "surma"
    assert sim("Zulfa", "Zulfa (feat. Dr. Zeus)") == 1.0
    assert sim("Song", "Song (Remix)") < 0.9           # versions stay distinct


def test_feat_title_is_matched_to_the_right_artists_song_not_the_exact_title_by_someone_else(make_mp3, tmp_path):
    f = make_mp3("Lamborghini.mp3", folder=tmp_path / "Incoming", title="Lamborghini", artist="Khan Bhaini/Shipra Goyal")
    prov = FakeProvider(
        albums={"B1": album("Lamborghini", ["Lamborghini (feat. Shipra Goyal)"], "Khan Bhaini")},
        song_results={"Lamborghini Khan Bhaini/Shipra Goyal": [
            song_hit("Lamborghini", "Diljit Sandhu", "Lamborghini", "B0", "v0"),
            song_hit("Lamborghini (feat. Shipra Goyal)", "Khan Bhaini", "Lamborghini", "B1", "v1")]})
    [row] = match_library([f], prov)
    assert row["status"] == "AUTO_MATCH" and row["matched_artist"] == "Khan Bhaini"


def test_no_candidate_with_title_and_artist_gives_accurate_reason(make_mp3, tmp_path):
    f = make_mp3("Surma.mp3", folder=tmp_path / "Incoming", title="Surma", artist="Khan Bhaini")
    prov = FakeProvider(song_results={"Surma Khan Bhaini": [
        song_hit("Surma", "Somebody Else", "Surma", "B0", "v0"),            # exact title, wrong artist
        song_hit("Completely Different Song", "Khan Bhaini", "Other", "B1", "v1")]})   # right artist, wrong title
    [row] = match_library([f], prov)
    assert row["status"] == "REVIEW"
    assert row["detail_status"] == "No candidate has both a close title and a matching artist"


def test_wrong_artist_only_candidate_is_reviewed_with_artist_reason(make_mp3, tmp_path):
    f = make_mp3("Surma.mp3", folder=tmp_path / "Incoming", title="Surma", artist="Khan Bhaini")
    prov = FakeProvider(song_results={"Surma Khan Bhaini": [song_hit("Surma", "Somebody Else", "Surma", "B0")]})
    [row] = match_library([f], prov)
    assert row["status"] == "REVIEW" and "artist credit does not agree" in row["detail_status"]


def test_same_release_track_in_two_different_files_is_flagged_not_renamed(make_mp3, tmp_path):
    tracks = ["Goriye", "Proposal"]
    inalbum = make_mp3("Proposal.mp3", folder=tmp_path / "Album - X", title="Proposal", artist="Mehtab Virk", album="X")
    loose = make_mp3("Proposal copy.mp3", folder=tmp_path / "loose", title="Proposal", artist="Mehtab Virk", album="X")
    loose.write_bytes(loose.read_bytes() + b"\x00" * 7)           # different bytes: not a byte duplicate
    real = album("X", tracks, "Mehtab Virk")
    prov = FakeProvider(albums={"B": real}, album_results={"X Mehtab Virk": [album_hit("X", "B", "Mehtab Virk")]})
    rows = match_library([inalbum, loose], prov)
    assert sorted(r["status"] for r in rows) == ["AUTO_MATCH", "REVIEW_DUPLICATE"]
    dup = next(r for r in rows if r["status"] == "REVIEW_DUPLICATE")
    assert dup["duplicate_of"] and "Same release track" in dup["detail_status"]
    assert dup["release_tracks"] == 2      # release size survives the flag, so layout can still group it
    assert inalbum.exists() and loose.exists()


class ListOrderProvider(FakeProvider):
    """Like JioSaavn: tracks come back in an arbitrary order and carry no track-number field."""
    name = "jiosaavn"

    def album_details(self, ref):
        d = super().album_details(ref)
        return {**d, "tracks": [{"song": t["song"], "primary_artists": t["primary_artists"]} for t in d["tracks"]]}


def _jumbled_album():
    # true order: A B C D, but the provider lists D A C B
    full = album("Alb", ["A", "B", "C", "D"], "Art")
    by = {t["song"]: t for t in full["tracks"]}
    return {**full, "tracks": [by[n] for n in ["D", "A", "C", "B"]]}


def test_provider_list_position_is_not_used_as_track_number(make_mp3, tmp_path):
    f = make_mp3("B.mp3", folder=tmp_path / "Alb", title="B", artist="Art", album="Alb")          # no local number
    prov = ListOrderProvider(albums={"X": _jumbled_album()},
                             album_results={"Alb Art": [album_hit("Alb", "X", "Art")]})
    [row] = match_library([f], prov)
    assert row["status"] == "AUTO_MATCH"
    assert row["track_number"] == "" and row["track_number_source"] == "none"    # list position would have said 4


def test_unreliable_provider_numbers_fall_back_to_the_files_own_track_tag(make_mp3, tmp_path):
    f = make_mp3("B.mp3", folder=tmp_path / "Alb", title="B", artist="Art", album="Alb", tracknumber="2")
    prov = ListOrderProvider(albums={"X": _jumbled_album()},
                             album_results={"Alb Art": [album_hit("Alb", "X", "Art")]})
    [row] = match_library([f], prov)
    assert row["track_number"] == "2" and row["track_number_source"] == "local"
    assert "Local track-number tag" not in row["detail_status"]       # no spurious conflict note


def test_explicit_provider_numbers_are_still_trusted(make_mp3, tmp_path):
    f = make_mp3("B.mp3", folder=tmp_path / "Alb", title="B", artist="Art", album="Alb")
    prov = FakeProvider(albums={"X": album("Alb", ["A", "B", "C"], "Art")},
                        album_results={"Alb Art": [album_hit("Alb", "X", "Art")]})
    [row] = match_library([f], prov)
    assert row["track_number"] == "2" and row["track_number_source"] == "provider"
