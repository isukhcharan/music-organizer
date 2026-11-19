import pytest
from mutagen import File as MutagenFile

import musicorganizer.organizer as organizer
from musicorganizer.journal import Journal
from musicorganizer.organizer import apply_plan, plan_moves, undo_job


def easy(p):
    return MutagenFile(p, easy=True)


def auto_row(source, **kw):
    row = {"status": "AUTO_MATCH", "source": str(source), "track_number": "2", "matched_title": "Wah Kya Nazare",
           "matched_artist": "Harnoor", "matched_album": "8 Chances", "matched_year": "2020",
           "local_album": "", "detail_status": "Matched against album tracklist (9 tracks); album coverage 100%.",
           "resolved_albumartist": "Harnoor", "albumartist_basis": "single consistent track artist"}
    row.update(kw)
    return row


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "state" / "journal.db")
    yield j
    j.close()


def test_plan_touches_nothing(make_mp3, tmp_path):
    src = make_mp3("Wah Kya Nazare.mp3", folder=tmp_path / "in", artist="Harnoor")
    out = tmp_path / "out"
    plan = plan_moves([auto_row(src)], out)
    assert plan.movable[0].dest == out / "Harnoor" / "8 Chances" / "02 - Wah Kya Nazare.mp3"
    assert src.exists() and not out.exists()


def test_only_auto_match_rows_move_and_others_stay(make_mp3, tmp_path):
    ok = make_mp3("a.mp3", folder=tmp_path / "in", title="A", artist="Harnoor")
    review = make_mp3("b.mp3", folder=tmp_path / "in", title="B")
    dup = make_mp3("c.mp3", folder=tmp_path / "in", title="C")
    rows = [auto_row(ok), {"status": "REVIEW", "source": str(review)},
            {"status": "REVIEW_DUPLICATE", "source": str(dup)}]
    plan = plan_moves(rows, tmp_path / "out")
    assert len(plan.untouched) == 2
    j = Journal(":memory:")
    apply_plan(plan, j)
    assert not ok.exists() and review.exists() and dup.exists()
    assert rows[0]["status"] == "MOVED" and rows[0]["output"]


def test_fills_blanks_but_never_overwrites_local_tags(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Local Title", artist="Local Artist", genre="Punjabi")
    plan = plan_moves([auto_row(src)], tmp_path / "out")
    apply_plan(plan, journal)
    tags = easy(plan.ops[0].dest)
    assert tags["title"] == ["Local Title"] and tags["artist"] == ["Local Artist"]
    assert tags["genre"] == ["Punjabi"]
    assert tags["album"] == ["8 Chances"] and tags["date"] == ["2020"] and tags["tracknumber"] == ["2"]


def test_collision_gets_suffix_and_existing_file_is_untouched(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Wah Kya Nazare", artist="Harnoor")
    existing = tmp_path / "out" / "Harnoor" / "8 Chances" / "02 - Wah Kya Nazare.mp3"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"precious")
    plan = plan_moves([auto_row(src)], tmp_path / "out")
    op = plan.ops[0]
    assert op.collision and op.dest.name == "02 - Wah Kya Nazare (2).mp3"
    apply_plan(plan, journal)
    assert existing.read_bytes() == b"precious" and op.dest.exists()


def test_two_planned_files_with_same_destination_do_not_clash(make_mp3, tmp_path):
    a = make_mp3("a.mp3", folder=tmp_path / "in1", title="Same", artist="X")
    b = make_mp3("b.mp3", folder=tmp_path / "in2", title="Same", artist="X")
    plan = plan_moves([auto_row(a), auto_row(b)], tmp_path / "out")
    assert plan.ops[0].dest != plan.ops[1].dest and plan.ops[1].collision


def test_failure_leaves_source_in_place_and_no_temp_files(make_mp3, tmp_path, journal, monkeypatch):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="T", artist="Harnoor")
    plan = plan_moves([auto_row(src)], tmp_path / "out")

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(organizer, "fill_blank_tags", boom)
    job = apply_plan(plan, journal)
    assert src.exists()
    assert not any(p.is_file() for p in (tmp_path / "out").rglob("*"))
    assert plan.ops[0].row["status"] == "APPLY_ERROR"
    assert journal.operations(job)[0]["status"] == "failed"


def test_sidecar_with_glob_characters_in_name_moves_with_its_track(make_mp3, tmp_path, journal):
    src = make_mp3("Song [Live].mp3", folder=tmp_path / "in", title="Song [Live]", artist="Harnoor")
    lrc = src.with_suffix(".lrc")
    lrc.write_text("lyrics")
    other = tmp_path / "in" / "Song [Live] other.lrc"
    other.write_text("not mine")
    plan = plan_moves([auto_row(src)], tmp_path / "out")
    apply_plan(plan, journal)
    assert plan.ops[0].sidecars[0][1].read_text() == "lyrics"
    assert other.exists()


def test_undo_restores_files_and_removes_empty_folders(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Wah", artist="Harnoor")
    plan = plan_moves([auto_row(src)], tmp_path / "out")
    job = apply_plan(plan, journal)
    dest = plan.ops[0].dest
    assert dest.exists() and not src.exists()
    result = undo_job(journal, job)
    assert result == {"undone": 1, "skipped": []}
    assert src.exists() and not dest.exists() and not dest.parent.exists()
    assert journal.operations(job)[0]["status"] == "undone"


def test_undo_refuses_when_original_spot_is_taken(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Wah", artist="Harnoor")
    plan = plan_moves([auto_row(src)], tmp_path / "out")
    job = apply_plan(plan, journal)
    src.write_bytes(b"someone put a different file here")
    result = undo_job(journal, job)
    assert result["undone"] == 0 and "occupied" in result["skipped"][0][1]
    assert src.read_bytes() == b"someone put a different file here" and plan.ops[0].dest.exists()


def test_missing_source_is_reported_not_crashed(tmp_path, journal):
    plan = plan_moves([auto_row(tmp_path / "gone.mp3")], tmp_path / "out")
    assert plan.ops[0].problem == "source file not found"
    apply_plan(plan, journal)


def test_undo_prunes_emptied_folders_but_stops_at_output_root(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Wah", artist="Harnoor")
    out = tmp_path / "out"
    keep = out / "Other Artist"
    keep.mkdir(parents=True)
    plan = plan_moves([auto_row(src)], out)
    job = apply_plan(plan, journal)
    assert (out / "Harnoor" / "8 Chances").exists()
    undo_job(journal, job, stop_at=out)
    assert not (out / "Harnoor").exists()       # album and artist folders both removed
    assert out.exists() and keep.exists()        # output root and unrelated folders untouched


def test_undo_restores_original_tags_after_fill_and_after_manual_overwrite(make_mp3, tmp_path, journal):
    from musicorganizer.resolver import plan_manual

    # fill-blanks: provider added album/date/etc. -> undo removes them again
    a = make_mp3("a.mp3", folder=tmp_path / "in", title="Wah", artist="Harnoor")
    plan = plan_moves([auto_row(a)], tmp_path / "out")
    job = apply_plan(plan, journal)
    assert "album" in easy(plan.ops[0].dest)
    undo_job(journal, job, stop_at=tmp_path / "out")
    tags = easy(a)
    assert tags["title"] == ["Wah"] and tags["artist"] == ["Harnoor"]
    assert "album" not in tags and "date" not in tags and "tracknumber" not in tags

    # manual overwrite: title/artist replaced -> undo brings back the ORIGINAL (junk) values
    b = make_mp3("b.mp3", folder=tmp_path / "in", title="AKHIL | RANG GORA (Official Video)", artist="Speed Records")
    row = {"status": "REVIEW", "source": str(b)}
    cand = {"title": "Rang Gora", "artist": "Akhil", "album_hint": "Rang Gora"}
    mplan = plan_manual(row, cand, tmp_path / "out2")
    mjob = apply_plan(mplan, journal)
    assert easy(mplan.ops[0].dest)["title"] == ["Rang Gora"]
    undo_job(journal, mjob, stop_at=tmp_path / "out2")
    tags = easy(b)
    assert tags["title"] == ["AKHIL | RANG GORA (Official Video)"] and tags["artist"] == ["Speed Records"]
    assert "album" not in tags


def test_plan_computes_album_artist_for_rows_promoted_after_the_scan(make_mp3, tmp_path):
    detail = "Matched against album tracklist (7 tracks); album coverage 100%."
    rows = []
    for i, who in enumerate(["Amrit Maan", "Mehtab Virk", "Nit Mann"]):
        p = make_mp3(f"{i}.mp3", folder=tmp_path / "in" / "Album - P", title=f"T{i}", artist=who)
        rows.append(auto_row(p, matched_album="PANJ-AAB, Vol. 1", matched_artist=who, detail_status=detail,
                             track_number=str(i + 1)))
    loose = make_mp3("loose.mp3", folder=tmp_path / "in", title="T1", artist="Mehtab Virk")
    dup = auto_row(loose, status="REVIEW_DUPLICATE", matched_album="PANJ-AAB, Vol. 1", matched_artist="Mehtab Virk",
                   detail_status="Verified in 7-track release", track_number="2")
    rows.append(dup)
    dup["status"] = "AUTO_MATCH"          # user chose "keep both" after the scan (no inference ran)
    plan = plan_moves(rows, tmp_path / "out")
    assert {op.dest.parent for op in plan.ops} == {tmp_path / "out" / "Compilations" / "PANJ-AAB, Vol. 1"}


def test_no_track_number_means_no_filename_prefix_and_no_invented_track_tag(make_mp3, tmp_path, journal):
    src = make_mp3("w.mp3", folder=tmp_path / "in", title="Wah", artist="Harnoor")
    plan = plan_moves([auto_row(src, track_number="", track_number_source="none")], tmp_path / "out")
    assert plan.ops[0].dest.name == "Wah.mp3"
    apply_plan(plan, journal)
    assert "tracknumber" not in easy(plan.ops[0].dest)
