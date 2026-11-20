import pytest
from mutagen.id3 import COMM, ID3, TCON, TPE2, TXXX

from musicorganizer.genres import genre_is_invalid, is_junk, map_itunes_genre
from musicorganizer.journal import Journal
from musicorganizer.organizer import undo_job
from musicorganizer.repair import apply_changes, plan_repair, read_state, scan_folder


@pytest.mark.parametrize("text", [
    "latest", "Latest", "latest\x00", "Downloaded From DjPunjab.Com", "Downloaded from DJJOhAL.Com",
    "RiskyjaTT.CoM", "www.songs.pk", "Full Song", "unknown", "123"])
def test_junk(text):
    assert is_junk(text)


@pytest.mark.parametrize("text", ["Bhangra", "Hip Hop", "Uk Drill", "Punjabi Pop", "Filmi", "Blues",
                                  "Hip-Hop/Rap", "Singer-Songwriter", "Great song, love it", ""])
def test_not_junk(text):
    assert not is_junk(text)


def test_genre_frames_with_any_junk_value_are_invalid():
    assert genre_is_invalid(["latest"]) and genre_is_invalid(["Bhangra", "djpunjab"])
    assert not genre_is_invalid(["Bhangra", "Hip Hop", "Pop"])


def test_itunes_mapping_ignores_catch_alls():
    assert map_itunes_genre("Hip-Hop/Rap") == "Hip Hop" and map_itunes_genre("Indian Pop") == "Punjabi Pop"
    assert map_itunes_genre("Worldwide") is None and map_itunes_genre("New Age") is None


def tag(path, genre=None, comment=None, artist="Karan Aujla"):
    id3 = ID3(path)
    id3.add(TPE2(encoding=3, text=artist))
    if genre:
        id3.add(TCON(encoding=3, text=genre))
    if comment:
        id3.add(TXXX(encoding=3, desc="comment", text=comment))
    id3.save(path)
    return path


def test_junk_comment_removed_but_real_comment_kept(make_mp3, tmp_path):
    a = tag(make_mp3("a.mp3", folder=tmp_path / "lib"), comment="latest")
    b = tag(make_mp3("b.mp3", folder=tmp_path / "lib"), comment="Live in Toronto, 2019")
    c = ID3(make_mp3("c.mp3", folder=tmp_path / "lib")); c.add(COMM(encoding=3, lang="eng", desc="", text="Downloaded From DjPunjab.Com")); c.save()
    states, _ = scan_folder(tmp_path / "lib")
    changes = {ch.path.name: ch for ch in plan_repair(states)}
    assert set(changes) == {"a.mp3", "c.mp3"}                       # b's real comment is left alone
    assert changes["a.mp3"].remove_comment_keys == ["TXXX:comment"]
    assert changes["c.mp3"].remove_comment_keys == ["COMM::eng"]


def test_genre_policy_valid_kept_junk_cleared_blank_filled_from_artist_majority(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    for i in range(3):
        tag(make_mp3(f"ok{i}.mp3", folder=lib), genre="Bhangra")        # artist's established genre
    keep = tag(make_mp3("keep.mp3", folder=lib), genre="Hip Hop")        # valid, different: must not change
    junk = tag(make_mp3("junk.mp3", folder=lib), genre="latest")         # invalid -> replaced by majority
    blank = tag(make_mp3("blank.mp3", folder=lib))                       # blank -> majority
    other = tag(make_mp3("other.mp3", folder=lib), artist="Nobody Known")  # no info -> stays blank
    states, _ = scan_folder(lib)
    ch = {c.path.name: c for c in plan_repair(states)}
    assert "keep.mp3" not in ch and "other.mp3" not in ch
    assert ch["junk.mp3"].new_genre == ["Bhangra"] and ch["junk.mp3"].genre_source == "library-artist"
    assert ch["blank.mp3"].new_genre == ["Bhangra"]


def test_junk_genre_with_no_source_is_cleared_to_blank(make_mp3, tmp_path):
    p = tag(make_mp3("x.mp3", folder=tmp_path / "lib"), genre="djpunjab", artist="Nobody Known")
    states, _ = scan_folder(tmp_path / "lib")
    [ch] = plan_repair(states)
    assert ch.clear_genre and ch.new_genre is None


def test_itunes_lookup_is_used_once_per_artist_and_only_when_needed(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    for i in range(3):
        tag(make_mp3(f"{i}.mp3", folder=lib), artist="Tarsem Jassar")
    asked = []
    states, _ = scan_folder(lib)
    changes = plan_repair(states, artist_lookup=lambda a: asked.append(a) or "Punjabi Pop")
    assert asked == ["Tarsem Jassar"] and all(c.genre_source == "itunes-artist" for c in changes) and len(changes) == 3


def test_apply_then_undo_restores_every_frame_and_audio(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    a = tag(make_mp3("a.mp3", folder=lib), genre="latest", comment="latest")
    b = ID3(make_mp3("b.mp3", folder=lib)); b.add(COMM(encoding=3, lang="eng", desc="", text="Downloaded From DjPunjab.Com")); b.save()
    for i in range(2):
        tag(make_mp3(f"ok{i}.mp3", folder=lib), genre="Bhangra")
    before = {p.name: p.read_bytes() for p in lib.glob("*.mp3")}
    states, _ = scan_folder(lib)
    changes = plan_repair(states)
    j = Journal(tmp_path / "s.db")
    job = apply_changes(changes, j)
    sa, sb = read_state(a), read_state(lib / "b.mp3")
    assert sa.genres == ["Bhangra"] and sa.comments == []
    assert sb.comments == [] and not any(p.name.startswith(".mo-tmp") for p in lib.iterdir())
    assert undo_job(j, job)["undone"] == len(changes)
    ra, rb = read_state(a), read_state(lib / "b.mp3")
    assert ra.genres == ["latest"] and ra.comments == [("TXXX:comment", ["latest"])]
    assert rb.comments == [("COMM::eng", ["Downloaded From DjPunjab.Com"])]
    j.close()


def test_non_mp3_and_untagged_files_are_reported_not_touched(make_mp3, tmp_path):
    lib = tmp_path / "lib"; lib.mkdir()
    (lib / "x.flac").write_bytes(b"not really flac")
    (lib / "cover.jpg").write_bytes(b"jpg")
    states, skipped = scan_folder(lib)
    assert states == [] and [p.name for p in skipped] == ["x.flac"]


def test_track_genre_overrides_a_more_generic_existing_genre_but_pop_never_does(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    tag(make_mp3("sad.mp3", folder=lib), genre="Bhangra")
    tag(make_mp3("pop.mp3", folder=lib), genre="Bhangra")
    tag(make_mp3("same.mp3", folder=lib), genre="Hip Hop")
    answers = {"sad": "Punjabi Pop", "pop": "Pop", "same": "Hip Hop"}
    states, _ = scan_folder(lib)
    ch = {c.path.stem: c for c in plan_repair(states, track_lookup=lambda a, t: answers[t])}
    assert ch["sad"].new_genre == ["Punjabi Pop"] and ch["sad"].replaced == ["Bhangra"]
    assert "pop" not in ch            # catch-all "Pop" must not replace "Bhangra"
    assert "same" not in ch           # nothing to change


def test_track_genre_beats_artist_majority_for_blanks(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    for i in range(3):
        tag(make_mp3(f"ok{i}.mp3", folder=lib), genre="Bhangra")
    tag(make_mp3("blank.mp3", folder=lib))
    states, _ = scan_folder(lib)
    ch = {c.path.stem: c for c in plan_repair(states, track_lookup=lambda a, t: "Hip Hop" if t == "blank" else None)}
    assert ch["blank"].new_genre == ["Hip Hop"] and ch["blank"].genre_source == "itunes-track"


def test_override_is_undoable(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    p = tag(make_mp3("sad.mp3", folder=lib, title="sad"), genre="Bhangra")
    states, _ = scan_folder(lib)
    j = Journal(tmp_path / "s.db")
    job = apply_changes(plan_repair(states, track_lookup=lambda a, t: "Punjabi Pop"), j)
    assert read_state(p).genres == ["Punjabi Pop"]
    undo_job(j, job)
    assert read_state(p).genres == ["Bhangra"]
    j.close()


def numbered(make_mp3, folder, name, artist="Karan Aujla", albumartist=None, year=None):
    from mutagen.id3 import TDRC as _TDRC, TPE1 as _TPE1
    path = make_mp3(name, folder=folder)
    id3 = ID3(path)
    id3.add(_TPE1(encoding=3, text=artist))
    if albumartist:
        id3.add(TPE2(encoding=3, text=albumartist))
    if year:
        id3.add(_TDRC(encoding=3, text=year))
    id3.save(path)
    return path


def test_blank_gaps_are_filled_from_folder_and_filename(make_mp3, tmp_path):
    f = tmp_path / "lib" / "Album"
    numbered(make_mp3, f, "01 Karan Aujla - Ashke.mp3", albumartist="Karan Aujla", year="2026-09-25")
    numbered(make_mp3, f, "02 Karan Aujla & Ikka - Rap.mp3", albumartist="Karan Aujla", year="2026-09-25")
    numbered(make_mp3, f, "03 Karan Aujla & Ikka - Blank.mp3", artist="Karan Aujla & Ikka")
    states, _ = scan_folder(tmp_path / "lib")
    ch = {c.path.name: c for c in plan_repair(states)}
    c = ch["03 Karan Aujla & Ikka - Blank.mp3"]
    assert c.fills == {"TPE2": "Karan Aujla", "TRCK": "3", "TDRC": "2026"}      # not "Karan Aujla & Ikka"


def test_fills_never_overwrite_and_unclear_cases_add_nothing(make_mp3, tmp_path):
    f = tmp_path / "lib" / "Mix"
    numbered(make_mp3, f, "01 A - x.mp3", artist="A")
    numbered(make_mp3, f, "01 B - y.mp3", artist="B")            # same number twice, two artists, no album artist
    states, _ = scan_folder(tmp_path / "lib")
    ch = {c.path.name: c for c in plan_repair(states)}
    for c in ch.values():
        assert "TPE2" not in c.fills and "TRCK" not in c.fills
        assert any("unclear" in n for n in c.fill_notes) and any("not unique" in n for n in c.fill_notes)


def test_fills_apply_and_undo_removes_only_what_was_added(make_mp3, tmp_path):
    f = tmp_path / "lib" / "Album"
    numbered(make_mp3, f, "01 Karan Aujla - Ashke.mp3", albumartist="Karan Aujla", year="2026")
    numbered(make_mp3, f, "03 Karan Aujla - Other.mp3", albumartist="Karan Aujla", year="2026")
    p = numbered(make_mp3, f, "02 Karan Aujla - Blank.mp3")
    states, _ = scan_folder(tmp_path / "lib")
    j = Journal(tmp_path / "s.db")
    job = apply_changes(plan_repair(states), j)
    after = read_state(p)
    assert (after.albumartist, after.track, after.year) == ("Karan Aujla", "2", "2026")
    undo_job(j, job)
    back = read_state(p)
    assert (back.albumartist, back.track, back.year) == ("", "", "")
    j.close()
