from mutagen import File as MutagenFile

from musicorganizer.journal import Journal
from musicorganizer.organizer import apply_plan
from musicorganizer.resolver import plan_manual, provider_values, resolve_values
from musicorganizer.store import Store

CAND = {"title": "Kade Ta Tu Avenga", "artist": "Karan Aujla", "album_hint": "Kade Ta Tu Avenga EP", "source": "ytmusic"}


def easy(p):
    return MutagenFile(p, easy=True)


def test_defaults_match_the_users_usual_choices():
    local = {"title": "kade ta tu avenga mp3", "artist": "k aujla", "album": "", "albumartist": "LA",
             "date": "2021", "tracknumber": "3", "genre": "Punjabi"}
    prov = provider_values(CAND, {"matched_year": "2022", "track_number": "1"})
    v = resolve_values(local, prov)
    assert v["title"] == "Kade Ta Tu Avenga"          # provider
    assert v["artist"] == "Karan Aujla"                # provider
    assert v["album"] == "Kade Ta Tu Avenga EP"        # provider
    assert v["albumartist"] is None                    # keep
    assert v["date"] == "2021"                         # local, not provider's 2022
    assert v["tracknumber"] == "3"                     # local, not provider's 1
    assert v["genre"] is None                          # keep


def test_blank_values_never_erase_and_overrides_work():
    local = {"title": "t", "artist": "", "album": "Local Album", "albumartist": "", "date": "", "tracknumber": "",
             "genre": ""}
    prov = {"title": "", "artist": "P", "album": "", "albumartist": "", "date": "", "tracknumber": "", "genre": ""}
    v = resolve_values(local, prov, {"genre": {"mode": "manual", "value": " Bhangra "}, "album": {"mode": "provider"}})
    assert v["title"] is None            # provider blank -> keep current tag
    assert v["album"] is None            # provider blank -> do not erase local album
    assert v["artist"] == "P"
    assert v["genre"] == "Bhangra"
    assert v["date"] is None             # local blank -> nothing to write


def test_manual_apply_overwrites_only_chosen_fields_and_uses_standard_layout(make_mp3, tmp_path):
    src = make_mp3("kade.mp3", folder=tmp_path / "in", title="kade ta", artist="k aujla", genre="Punjabi",
                   tracknumber="4")
    row = {"status": "REVIEW", "source": str(src), "matched_year": "2022"}
    plan = plan_manual(row, CAND, tmp_path / "out")
    op = plan.ops[0]
    assert op.dest == tmp_path / "out" / "Karan Aujla" / "Kade Ta Tu Avenga EP" / "04 - Kade Ta Tu Avenga.mp3"
    j = Journal(":memory:")
    apply_plan(plan, j)
    tags = easy(op.dest)
    assert tags["title"] == ["Kade Ta Tu Avenga"] and tags["artist"] == ["Karan Aujla"]
    assert tags["genre"] == ["Punjabi"] and tags["tracknumber"] == ["4"]     # kept
    assert "date" not in tags                                              # default is local; local was blank
    assert not src.exists()


def test_store_persists_rows_and_decisions_across_reopen(tmp_path):
    db = tmp_path / "state.db"
    s = Store(db)
    s.create_job("j1", tmp_path / "in", tmp_path / "out")
    s.save_rows("j1", [{"source": "a", "status": "AUTO_MATCH"}, {"source": "b", "status": "REVIEW"},
                       {"source": "c", "status": "REVIEW_DUPLICATE"}])
    s.save_decision("j1", "b", CAND, {"genre": {"mode": "manual", "value": "x"}})
    s.set_state("j1", "scanned")
    s.close()

    s = Store(db)
    assert s.get_job("j1")["state"] == "scanned"
    assert s.counts("j1") == {"AUTO_MATCH": 1, "REVIEW": 1, "REVIEW_DUPLICATE": 1}
    assert [r["source"] for r in s.pending_review("j1")] == ["c"]          # b already decided
    d = s.get_decision("j1", "b")
    assert d["candidate"]["title"] == CAND["title"] and not d["applied"]
    s.mark_applied("j1", "b")
    assert s.get_decision("j1", "b")["applied"]
    assert s.load_rows("j1", ("AUTO_MATCH",))[0]["source"] == "a"
