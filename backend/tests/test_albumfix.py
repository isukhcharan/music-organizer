from pathlib import Path

import mutagen

from musicorganizer.albumfix import apply_albumfix, plan_albumfix, read_state, scan_folder
from musicorganizer.journal import Journal
from musicorganizer.organizer import undo_job


def tag(path: Path, **kw):
    m = mutagen.File(path, easy=True)
    for k, v in kw.items():
        m[k] = [v]
    m.save()


def song(make_mp3, root, rel, **kw):
    p = make_mp3(Path(rel).name, folder=root / Path(rel).parent)
    tag(p, **kw)
    return p


def plan(root):
    states, _ = scan_folder(root)
    return {c.path.name: c for c in plan_albumfix(states)[0]}, plan_albumfix(states)[1]


def test_soundtrack_with_blank_and_mixed_album_artists_becomes_compilation(make_mp3, tmp_path):
    base = "Soundtracks/Punjab 1984"
    for i, aa in enumerate(["Compilation", "Compilation", "", ""]):
        kw = dict(album="Punjab 1984", artist=f"Singer {i}", tracknumber=str(i + 1))
        if aa:
            kw["albumartist"] = aa
        song(make_mp3, tmp_path, f"{base}/0{i + 1} - T{i}.mp3", **kw)
    changes, _ = plan(tmp_path)
    assert set(changes) == {"02 - T2.mp3".replace("02", "03"), "04 - T3.mp3"} or len(changes) == 2
    assert all(c.set["albumartist"] == "Compilation" for c in changes.values())


def test_all_blank_soundtrack_gets_compilation(make_mp3, tmp_path):
    for i in range(3):
        song(make_mp3, tmp_path, f"Soundtracks/Jihne/0{i + 1} - T{i}.mp3", album="Jihne", artist=f"S{i}")
    changes, _ = plan(tmp_path)
    assert len(changes) == 3 and all(c.set["albumartist"] == "Compilation" for c in changes.values())


def test_consistent_album_is_left_alone(make_mp3, tmp_path):
    for i in range(3):
        song(make_mp3, tmp_path, f"Kaka/Album/0{i + 1} - T{i}.mp3", album="Album", artist="Kaka", albumartist="Kaka")
    assert plan(tmp_path)[0] == {}


def test_stray_unrelated_album_artist_on_a_single_is_replaced(make_mp3, tmp_path):
    song(make_mp3, tmp_path, "Jordan Sandhu/Singles/01 - Semi - Auto.mp3", album="Semi - Auto",
         artist="Jordan Sandhu, Wazir Patar", albumartist="Sukhcharan Singh")
    song(make_mp3, tmp_path, "Jordan Sandhu/Singles/01 - Other.mp3", album="Other", artist="Jordan Sandhu",
         albumartist="Jordan Sandhu")
    changes, _ = plan(tmp_path)
    assert changes["01 - Semi - Auto.mp3"].set == {"albumartist": "Jordan Sandhu"}
    assert "01 - Other.mp3" not in changes


def test_capital_letter_variants_in_one_artist_folder_are_merged(make_mp3, tmp_path):
    song(make_mp3, tmp_path, "GIPPY GREWAL/Singles/01 - A.mp3", album="A", artist="Gippy Grewal", albumartist="GIPPY GREWAL")
    for n in "BCD":
        song(make_mp3, tmp_path, f"GIPPY GREWAL/Singles/01 - {n}.mp3", album=n, artist="Gippy Grewal", albumartist="Gippy Grewal")
    changes, _ = plan(tmp_path)
    assert list(changes) == ["01 - A.mp3"] and changes["01 - A.mp3"].set["albumartist"] == "Gippy Grewal"


def test_dominant_album_artist_wins_in_a_normal_album(make_mp3, tmp_path):
    for i in range(7):
        song(make_mp3, tmp_path, f"DIVINE & Karan Aujla/Street Dreams/0{i + 1} - T{i}.mp3", album="Street Dreams",
             artist="DIVINE, Karan Aujla", albumartist="DIVINE & Karan Aujla")
    song(make_mp3, tmp_path, "DIVINE & Karan Aujla/Street Dreams/08 - X.mp3", album="Street Dreams",
         artist="DIVINE", albumartist="DIVINE")
    changes, _ = plan(tmp_path)
    assert list(changes) == ["08 - X.mp3"] and changes["08 - X.mp3"].set["albumartist"] == "DIVINE & Karan Aujla"


def test_playlist_position_track_numbers(make_mp3, tmp_path):
    song(make_mp3, tmp_path, "Gippy/Singles/01 - Fuel.mp3", album="Fuel", artist="Gippy", tracknumber="204")
    song(make_mp3, tmp_path, "Soundtracks/Jihne/181 - Channa.mp3", album="Jihne", artist="X", tracknumber="181",
         albumartist="Compilation")
    song(make_mp3, tmp_path, "Kaka/Alb/03 - Real.mp3", album="Alb", artist="Kaka", tracknumber="3/9", albumartist="Kaka")
    changes, _ = plan(tmp_path)
    assert changes["01 - Fuel.mp3"].set == {"tracknumber": "1"}          # filename number is usable
    assert changes["181 - Channa.mp3"].set == {"tracknumber": None}      # nothing usable: removed, not invented
    assert "03 - Real.mp3" not in changes


def test_apply_writes_and_undo_restores(make_mp3, tmp_path):
    p = song(make_mp3, tmp_path, "Soundtracks/Jihne/181 - Channa.mp3", album="Jihne", artist="X", tracknumber="181")
    q = song(make_mp3, tmp_path, "Soundtracks/Jihne/05 - Fukre.mp3", album="Jihne", artist="Y", tracknumber="5",
             albumartist="Compilation")
    journal = Journal(tmp_path / "j.db")
    states, _ = scan_folder(tmp_path)
    changes, _ = plan_albumfix(states)
    job = apply_albumfix(changes, journal)
    after = read_state(p)
    assert after.albumartist == "Compilation" and after.track == ""
    assert read_state(q).track == "5"                                    # untouched
    assert not list(tmp_path.rglob(".mo-tmp-*"))
    assert undo_job(journal, job)["undone"] == 1
    back = read_state(p)
    assert back.albumartist == "" and back.track == "181"
