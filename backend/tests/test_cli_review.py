import json
from argparse import Namespace

import pytest
from mutagen import File

from musicorganizer.cli import cmd_apply_reviewed, cmd_review
from musicorganizer.journal import Journal
from musicorganizer.resolver import parse_overrides
from musicorganizer.store import Store


def test_parse_overrides():
    assert parse_overrides("title=l album=p genre=m:Punjabi date=k") == {
        "title": {"mode": "local"}, "album": {"mode": "provider"},
        "genre": {"mode": "manual", "value": "Punjabi"}, "date": {"mode": "keep"}}
    assert parse_overrides('genre="m:Punjabi Pop"')["genre"]["value"] == "Punjabi Pop"
    assert parse_overrides("") == {}
    for bad in ("nope=l", "title=x", "title"):
        with pytest.raises(ValueError):
            parse_overrides(bad)


def scripted(answers):
    it = iter(answers)
    return lambda prompt="": next(it)


@pytest.fixture
def env(tmp_path, make_mp3):
    store, journal = Store(tmp_path / "s.db"), Journal(tmp_path / "s.db")
    src = make_mp3("AKHIL _ RANG GORA (Official Video).mp3", folder=tmp_path / "in",
                   title="AKHIL | RANG GORA (Official Video)", artist="Speed Records", genre="Punjabi")
    cands = [{"source": "ytmusic", "title": "Rang Gora", "artist": "Akhil", "album_hint": "Rang Gora", "score": 0.5}]
    row = {"status": "REVIEW", "source": str(src), "local_title": "AKHIL | RANG GORA", "local_artist": "Speed Records",
           "detail_status": "Top search title is not a close enough match", "candidates": json.dumps(cands)}
    store.create_job("J", tmp_path / "in", tmp_path / "out")
    store.save_rows("J", [row])
    yield store, journal, src, tmp_path
    store.close()
    journal.close()


def test_review_saves_decision_and_moves_nothing_until_applied(env):
    store, journal, src, tmp = env
    cmd_review(Namespace(job="J"), store, journal, ask=scripted(["1", "genre=k", ""]))
    assert src.exists()                                        # review never moves files
    assert store.get_decision("J", str(src))["candidate"]["title"] == "Rang Gora"
    assert store.pending_review("J") == []

    assert cmd_apply_reviewed(Namespace(job="J", yes=False), store, journal) == 2 and src.exists()
    assert cmd_apply_reviewed(Namespace(job="J", yes=True), store, journal) == 0
    dest = tmp / "out" / "Akhil" / "Rang Gora" / "Rang Gora.mp3"
    assert dest.exists() and not src.exists()
    tags = File(dest, easy=True)
    assert tags["title"] == ["Rang Gora"] and tags["artist"] == ["Akhil"] and tags["genre"] == ["Punjabi"]
    assert store.get_decision("J", str(src))["applied"]


def test_skip_and_bad_override_do_not_save(env):
    store, journal, src, _ = env
    cmd_review(Namespace(job="J"), store, journal, ask=scripted(["1", "bogus=x", "s"]))
    assert store.get_decision("J", str(src)) is None and len(store.pending_review("J")) == 1


def test_duplicate_keep_both_marks_row_approved(tmp_path):
    store, journal = Store(tmp_path / "s.db"), Journal(tmp_path / "s.db")
    store.create_job("J", tmp_path, tmp_path / "out")
    store.save_rows("J", [{"status": "REVIEW_DUPLICATE", "source": "x.mp3", "duplicate_of": "y.mp3",
                           "local_title": "x", "detail_status": "dup"}])
    cmd_review(Namespace(job="J"), store, journal, ask=scripted(["b"]))
    assert store.load_rows("J")[0]["status"] == "AUTO_MATCH"
    store.close()
    journal.close()
