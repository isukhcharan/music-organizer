import json
import time

import pytest
from fastapi.testclient import TestClient
from mutagen import File

from musicorganizer.api import create_app


class Fake:
    name = "ytmusic"

    def __init__(self):
        self.tracks = ["Ashke", "Real Bad Man", "Straight Up"]

    def search_songs(self, q):
        if "rang gora" in q.lower() or "akhil" in q.lower():
            return [{"source": "ytmusic", "title": "Rang Gora", "artist": "Akhil", "id": "v9", "url": "",
                     "year": "2018", "album_hint": "Rang Gora", "browse_id": ""}]
        return []

    def search_albums(self, q):
        if "AUJLA SZN 1" in q:
            return [{"source": "ytmusic", "title": "AUJLA SZN 1", "artist": "Karan Aujla", "id": "B1", "url": "",
                     "browse_id": "B1"}]
        return []

    def album_details(self, ref):
        return {"album": "AUJLA SZN 1", "album_artist": "", "provider_artist": "Karan Aujla", "year": "2026",
                "tracks": [{"song": t, "primary_artists": "Karan Aujla", "sequence": str(i)}
                           for i, t in enumerate(self.tracks, 1)]}

    def album_for_song(self, c):
        return None


@pytest.fixture
def env(tmp_path, make_mp3):
    inp = tmp_path / "in"
    for t in ["Ashke", "Real Bad Man", "Straight Up"]:
        make_mp3(f"{t}.mp3", folder=inp / "AUJLA SZN 1", title=t, artist="Karan Aujla", album="AUJLA SZN 1")
    make_mp3("AKHIL _ RANG GORA (Official Video).mp3", folder=inp, title="AKHIL | RANG GORA (Official Video)",
             artist="Speed Records")
    app = create_app(tmp_path / "state.db", provider_factory=lambda name: Fake())
    return TestClient(app), inp, tmp_path / "out", tmp_path


def wait_scanned(client, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in {"scanned", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def start(client, inp, out):
    r = client.post("/api/jobs", json={"input_dir": str(inp), "output_dir": str(out)})
    assert r.status_code == 201, r.text
    job = r.json()["id"]
    return job, wait_scanned(client, job)


def test_only_localhost_may_talk_to_the_api(env):
    client, *_ = env
    assert client.get("/api/status").json()["ok"]
    assert client.get("/api/status", headers={"Host": "evil.example.com"}).status_code == 403
    assert client.get("/api/status", headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert client.get("/api/status", headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_job_validation(env, tmp_path):
    client, inp, out, tmp = env
    bad = lambda **kw: client.post("/api/jobs", json={"input_dir": str(inp), "output_dir": str(out), **kw})
    assert client.post("/api/jobs", json={"input_dir": str(tmp / "nope"), "output_dir": str(out)}).status_code == 400
    assert client.post("/api/jobs", json={"input_dir": str(inp), "output_dir": str(inp)}).status_code == 400
    assert client.post("/api/jobs", json={"input_dir": str(inp), "output_dir": str(tmp / "x" / "y")}).status_code == 400
    assert bad(provider="spotify").status_code == 400
    empty = tmp / "empty"
    empty.mkdir()
    assert client.post("/api/jobs", json={"input_dir": str(empty), "output_dir": str(out)}).status_code == 400


def test_scan_results_review_queue_and_progress(env):
    client, inp, out, _ = env
    job_id, job = start(client, inp, out)
    assert job["counts"] == {"AUTO_MATCH": 3, "REVIEW": 1}
    assert job["pending_review"] == 1 and job["progress"]["done"] == job["progress"]["total"] == 4
    items = client.get(f"/api/jobs/{job_id}/tracks", params={"status": "AUTO_MATCH"}).json()
    assert items["total"] == 3 and "candidates" not in items["items"][0]
    q = client.get(f"/api/jobs/{job_id}/review").json()
    assert q["defaults"]["title"] == "provider" and q["defaults"]["date"] == "local"
    assert q["items"][0]["candidates"][0]["title"] == "Rang Gora"
    assert q["items"][0]["local"]["artist"] == "Speed Records"


def test_websocket_streams_state_until_done(env):
    client, inp, out, _ = env
    r = client.post("/api/jobs", json={"input_dir": str(inp), "output_dir": str(out)})
    job_id = r.json()["id"]
    last = None
    with client.websocket_connect(f"/api/jobs/{job_id}/ws") as ws:
        while True:
            try:
                last = ws.receive_json()
            except Exception:
                break
    assert last["state"] == "scanned" and last["progress"]["done"] == 4


def test_nothing_moves_without_confirm_and_plan_is_a_pure_preview(env):
    client, inp, out, _ = env
    job_id, _ = start(client, inp, out)
    plan = client.get(f"/api/jobs/{job_id}/plan").json()
    assert plan["will_move"] == 3 and plan["stay"] == 1 and plan["stay_by_status"] == {"REVIEW": 1}
    assert plan["ops"][0]["dest"].endswith("01 - Ashke.mp3")
    assert client.post(f"/api/jobs/{job_id}/apply", json={}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/apply", json={"confirm": False}).status_code == 400
    assert len(list(inp.rglob("*.mp3"))) == 4 and not out.exists()


def test_review_preview_decision_apply_and_undo_end_to_end(env):
    client, inp, out, _ = env
    job_id, _ = start(client, inp, out)
    src = client.get(f"/api/jobs/{job_id}/review").json()["items"][0]["source"]
    body = {"source": src, "candidate_index": 0, "choices": {"genre": {"mode": "manual", "value": "Punjabi Pop"}}}
    pv = client.post(f"/api/jobs/{job_id}/review/preview", json=body).json()
    assert pv["values"]["title"] == "Rang Gora" and pv["values"]["genre"] == "Punjabi Pop"
    assert pv["destination"].endswith("Rang Gora.mp3") and "Akhil" in pv["destination"]
    assert client.post(f"/api/jobs/{job_id}/review/preview", json={**body, "candidate_index": 99}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/review/preview",
                       json={**body, "choices": {"title": {"mode": "hack"}}}).status_code == 400
    assert client.post(f"/api/jobs/{job_id}/review/decision", json=body).json() == {"saved": True, "pending": 0}

    assert client.post(f"/api/jobs/{job_id}/apply", json={"confirm": True}).json()["results"] == {"done": 3}
    assert client.post(f"/api/jobs/{job_id}/apply-reviewed", json={"confirm": True}).json() == {"moved": 1, "failed": 0}
    moved = out / "Akhil" / "Rang Gora" / "Rang Gora.mp3"
    assert moved.exists() and File(moved, easy=True)["genre"] == ["Punjabi Pop"]
    assert not list(inp.rglob("*.mp3"))
    assert len(client.get(f"/api/jobs/{job_id}/operations").json()) == 4
    assert "local_title" in client.get(f"/api/jobs/{job_id}/report.csv").text

    assert client.post(f"/api/jobs/{job_id}/undo", json={}).status_code == 400
    undone = client.post(f"/api/jobs/{job_id}/undo", json={"confirm": True}).json()
    assert undone == {"undone": 4, "skipped": []}
    assert len(list(inp.rglob("*.mp3"))) == 4        # everything is back where it came from
    assert not any(out.rglob("*.mp3")) and not any(out.rglob("*"))   # and the output folder is empty again


def test_decision_picks_candidate_by_index_and_rejects_unknown_tracks(env):
    client, inp, out, _ = env
    job_id, _ = start(client, inp, out)
    r = client.post(f"/api/jobs/{job_id}/review/decision",
                    json={"source": "C:/not/a/track.mp3", "candidate_index": 0, "choices": {}})
    assert r.status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404


def test_folder_browser_lists_only_directories(env):
    client, inp, out, tmp = env
    r = client.get("/api/fs/list", params={"path": str(inp)}).json()
    assert [d["name"] for d in r["dirs"]] == ["AUJLA SZN 1"] and r["audio_files_here"] == 1
    assert client.get("/api/fs/list", params={"path": str(inp / "AKHIL _ RANG GORA (Official Video).mp3")}).status_code == 400
    assert client.get("/api/fs/list").json()["dirs"]          # drive / root list


def test_cleanup_after_apply_only_touches_the_input_folder(env):
    import shutil
    client, inp, out, tmp = env
    (inp / "Empty Album").mkdir()
    (inp / "AUJLA SZN 1" / "album.nfo").write_text("x")             # residue next to the songs
    (inp / "AUJLA SZN 1" / "folder.jpg").write_bytes(b"jpg")
    trashed = []
    client = TestClient(create_app(tmp / "state2.db", provider_factory=lambda n: Fake(),
                                   trash=lambda p: (trashed.append(p), shutil.rmtree(p))))
    job_id, _ = start(client, inp, out)
    review_src = client.get(f"/api/jobs/{job_id}/review").json()["items"][0]["source"]
    pre = client.get(f"/api/jobs/{job_id}/cleanup").json()
    assert pre["trash_available"] and "AUJLA SZN 1" not in [d["path"] for d in pre["dirs"]]   # still holds songs
    assert [d["kind"] for d in pre["dirs"]] == ["empty"]
    client.post(f"/api/jobs/{job_id}/apply", json={"confirm": True})
    after = client.get(f"/api/jobs/{job_id}/cleanup").json()["dirs"]
    album = next(d for d in after if d["path"].endswith("AUJLA SZN 1"))
    assert album["kind"] == "leftovers" and album["has_images"] and not album["default_selected"]
    assert sorted(album["files"]) == ["album.nfo", "folder.jpg"]
    assert client.post(f"/api/jobs/{job_id}/cleanup", json={"paths": [album["path"]]}).status_code == 400   # needs confirm
    r = client.post(f"/api/jobs/{job_id}/cleanup", json={"confirm": True, "paths": [d["path"] for d in after]}).json()
    assert r["removed_empty"] == 1 and r["trashed"] == 1 and r["skipped"] == []
    assert inp.exists() and out.exists() and (inp / "AKHIL _ RANG GORA (Official Video).mp3").exists()
    assert not (inp / "AUJLA SZN 1").exists() and not (inp / "Empty Album").exists()
    # a path outside the input folder is refused, whatever the client sends
    r = client.post(f"/api/jobs/{job_id}/cleanup", json={"confirm": True, "paths": [str(out)]}).json()
    assert r["removed_empty"] == 0 and r["skipped"] and out.exists()


def test_native_folder_dialog(env, monkeypatch):
    import subprocess
    client, inp, *_ = env
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, fake_run.code, stdout=fake_run.out, stderr="")

    monkeypatch.setattr("musicorganizer.api.subprocess.run", fake_run)
    fake_run.code, fake_run.out = 0, "C:/Music\n"
    assert client.post("/api/fs/pick", json={"initial": str(inp)}).json() == {"path": "C:/Music", "cancelled": False}
    assert calls[0][3] == str(inp)                              # starts in the folder already typed in
    fake_run.out = "\n"                                          # user pressed Cancel
    assert client.post("/api/fs/pick", json={}).json() == {"path": None, "cancelled": True}
    assert calls[1][3] == ""                                     # a non-existent initial path is not passed on
    fake_run.code = 3                                            # no GUI toolkit installed
    assert client.post("/api/fs/pick", json={}).status_code == 501
    assert client.post("/api/fs/pick", json={}, headers={"Origin": "https://evil.example.com"}).status_code == 403


def test_scan_failure_is_reported_not_raised(tmp_path, make_mp3):
    make_mp3("a.mp3", folder=tmp_path / "in", title="A")

    def boom(name):
        raise RuntimeError("provider exploded")

    client = TestClient(create_app(tmp_path / "s.db", provider_factory=boom))
    r = client.post("/api/jobs", json={"input_dir": str(tmp_path / "in"), "output_dir": str(tmp_path / "out")})
    job = wait_scanned(client, r.json()["id"])
    assert job["state"] == "failed" and "provider exploded" in job["progress"]["error"]


def test_keep_both_promotes_a_duplicate(env, make_mp3):
    client, inp, out, tmp = env
    import shutil
    a = inp / "AUJLA SZN 1" / "Ashke.mp3"
    shutil.copy(a, inp / "Ashke copy.mp3")
    job_id, job = start(client, inp, out)
    dup = next(r for r in client.get(f"/api/jobs/{job_id}/tracks", params={"status": "REVIEW_DUPLICATE"}).json()["items"])
    assert client.post(f"/api/jobs/{job_id}/duplicates/keep-both", json={"source": dup["source"]}).json()["saved"]
    assert client.post(f"/api/jobs/{job_id}/duplicates/keep-both", json={"source": dup["source"]}).status_code == 400
