import hashlib
import shutil

import pytest
from fastapi.testclient import TestClient
from mutagen import File
from mutagen.id3 import ID3, TCON, TXXX

from musicorganizer.api import create_app
from musicorganizer.journal import Journal
from musicorganizer.organizer import apply_plan, plan_moves, undo_job
from musicorganizer.repair import read_state
from tests.test_api import Fake, start


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def row(src, **kw):
    r = {"status": "AUTO_MATCH", "source": str(src), "track_number": "2", "matched_title": "Wah Kya Nazare",
         "matched_artist": "Harnoor", "matched_album": "8 Chances", "matched_year": "2020", "local_album": "",
         "detail_status": "Matched against album tracklist (9 tracks); album coverage 100%.",
         "resolved_albumartist": "Harnoor", "albumartist_basis": "single consistent track artist"}
    r.update(kw)
    return r


def dirty(make_mp3, folder):
    p = make_mp3("w.mp3", folder=folder, title="Wah", artist="Harnoor")
    id3 = ID3(p)
    id3.add(TCON(encoding=3, text="latest"))
    id3.add(TXXX(encoding=3, desc="comment", text="Downloaded From DjPunjab.Com"))
    id3.save(p)
    (p.with_suffix(".lrc")).write_text("lyrics")
    return p


def test_copy_leaves_the_original_byte_identical_and_cleans_only_the_copy(make_mp3, tmp_path):
    src = dirty(make_mp3, tmp_path / "in")
    before = digest(src)
    plan = plan_moves([row(src)], tmp_path / "out")
    j = Journal(tmp_path / "s.db")
    apply_plan(plan, j, mode="copy")
    dest = plan.ops[0].dest
    assert src.exists() and digest(src) == before                          # original untouched, junk and all
    assert src.with_suffix(".lrc").exists()                                 # sidecar copied, not moved
    assert dest.exists() and dest.with_suffix(".lrc").read_text() == "lyrics"   # sidecar follows the renamed song
    assert dest.name == "02 - Wah.mp3" and dest.with_suffix(".lrc").name == "02 - Wah.lrc"
    copy = read_state(dest)
    assert copy.genres == [] and copy.comments == []                        # the copy arrives without junk
    assert read_state(src).genres == ["latest"] and read_state(src).comments    # while the original keeps it
    assert File(dest, easy=True)["album"] == ["8 Chances"]                  # and is tagged
    assert plan.ops[0].row["status"] == "COPIED"
    j.close()


def test_undo_of_a_copy_deletes_only_our_unmodified_copy(make_mp3, tmp_path):
    src = dirty(make_mp3, tmp_path / "in")
    plan = plan_moves([row(src)], tmp_path / "out")
    j = Journal(tmp_path / "s.db")
    job = apply_plan(plan, j, mode="copy")
    res = undo_job(j, job, stop_at=tmp_path / "out")
    assert res["undone"] == 2 and res["skipped"] == []                      # the audio copy and its sidecar
    assert src.exists() and not any((tmp_path / "out").rglob("*")) and (tmp_path / "out").exists()
    j.close()


def test_undo_refuses_to_delete_a_copy_the_user_has_since_edited(make_mp3, tmp_path):
    src = dirty(make_mp3, tmp_path / "in")
    plan = plan_moves([row(src)], tmp_path / "out")
    j = Journal(tmp_path / "s.db")
    job = apply_plan(plan, j, mode="copy")
    dest = plan.ops[0].dest
    id3 = ID3(dest)
    id3.add(TCON(encoding=3, text="My Own Genre"))
    id3.save(dest)                                                          # user edits the copy later
    res = undo_job(j, job, stop_at=tmp_path / "out")
    assert dest.exists() and any("modified" in why for _, why in res["skipped"])
    j.close()


def test_move_mode_is_unchanged(make_mp3, tmp_path):
    src = dirty(make_mp3, tmp_path / "in")
    plan = plan_moves([row(src)], tmp_path / "out")
    j = Journal(tmp_path / "s.db")
    apply_plan(plan, j)                                                      # default stays "move"
    assert not src.exists() and plan.ops[0].row["status"] == "MOVED"
    with pytest.raises(ValueError):
        apply_plan(plan, j, mode="link")
    j.close()


def test_api_copy_mode_end_to_end(tmp_path, make_mp3):
    inp = tmp_path / "in"
    for t in ["Ashke", "Real Bad Man", "Straight Up"]:
        make_mp3(f"{t}.mp3", folder=inp / "AUJLA SZN 1", title=t, artist="Karan Aujla", album="AUJLA SZN 1")
    (inp / "AUJLA SZN 1" / "album.nfo").write_text("x")
    originals = {p: digest(p) for p in inp.rglob("*") if p.is_file()}
    client = TestClient(create_app(tmp_path / "s.db", provider_factory=lambda n: Fake(), trash=None))
    job_id, _ = start(client, inp, tmp_path / "out")
    assert client.post(f"/api/jobs/{job_id}/apply", json={"confirm": True, "mode": "bogus"}).status_code == 400
    r = client.post(f"/api/jobs/{job_id}/apply", json={"confirm": True, "mode": "copy"}).json()
    assert r["results"] == {"done": 3}
    assert {p: digest(p) for p in inp.rglob("*") if p.is_file()} == originals          # input folder untouched
    assert len(list((tmp_path / "out").rglob("*.mp3"))) == 3
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["applied_mode"] == "copy" and job["counts"].get("COPIED") == 3
    # cleanup makes no sense after a copy: nothing was removed from the input
    pre = client.get(f"/api/jobs/{job_id}/cleanup").json()
    assert pre["copied"] and pre["dirs"] == []
    assert client.post(f"/api/jobs/{job_id}/cleanup", json={"confirm": True, "paths": [str(inp / "AUJLA SZN 1")]}).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/undo", json={"confirm": True}).json()["undone"] == 3
    assert not any((tmp_path / "out").rglob("*.mp3")) and {p: digest(p) for p in inp.rglob("*") if p.is_file()} == originals
