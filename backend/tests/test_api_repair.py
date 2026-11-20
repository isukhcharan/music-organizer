import time

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import COMM, ID3, TCON, TPE1, TXXX

from musicorganizer.api import create_app
from musicorganizer.repair import read_state


def make_lib(make_mp3, tmp_path):
    lib = tmp_path / "lib"
    paths = {}
    for name, genre, comment in [("a", None, "latest"), ("b", "latest", "Downloaded From DjPunjab.Com"),
                                 ("c", "Bhangra", None), ("d", "Bhangra", "Live in Toronto")]:
        p = make_mp3(f"{name}.mp3", folder=lib, title=name)
        id3 = ID3(p)
        id3.add(TPE1(encoding=3, text="Karan Aujla"))
        if genre:
            id3.add(TCON(encoding=3, text=genre))
        if comment:
            id3.add(TXXX(encoding=3, desc="comment", text=comment))
        id3.save(p)
        paths[name] = p
    return lib, paths


@pytest.fixture
def client(tmp_path):
    answers = {"a": "Hip-Hop/Rap", "c": "Punjabi Pop"}
    # same shape as TrackGenreLookup: (artist, title) -> mapped genre
    lookup = lambda artist, title: {"a": "Hip Hop", "c": "Punjabi Pop"}.get(title)
    return TestClient(create_app(tmp_path / "s.db", provider_factory=lambda n: None, genre_lookup=lookup))


def wait_ready(client, rid):
    end = time.time() + 20
    while time.time() < end:
        s = client.get(f"/api/repair/{rid}").json()
        if s["state"] in {"ready", "failed"}:
            return s
        time.sleep(0.05)
    raise AssertionError("repair preview did not finish")


def test_preview_apply_and_undo_through_the_api(client, make_mp3, tmp_path):
    lib, p = make_lib(make_mp3, tmp_path)
    before = {n: (read_state(x).genres, read_state(x).comments) for n, x in p.items()}
    rid = client.post("/api/repair", json={"folder": str(lib)}).json()["id"]
    s = wait_ready(client, rid)
    assert s["state"] == "ready" and s["progress"]["done"] == s["progress"]["total"] == 4
    assert s["summary"]["junk_comments"] == 2 and s["summary"]["junk_genres_cleared"] == 1
    assert s["summary"]["genres_overridden"] == 1                              # c: Bhangra -> Punjabi Pop
    items = {i["path"]: i for i in client.get(f"/api/repair/{rid}/changes").json()["items"]}
    assert items["a.mp3"]["removed_comments"] == ["latest"] and items["a.mp3"]["new_genre"] == ["Hip Hop"]
    assert items["c.mp3"]["overrides"] and items["c.mp3"]["old_genre"] == ["Bhangra"]
    d = items["d.mp3"]                                                         # real comment + valid genre are kept;
    assert d["removed_comments"] == [] and d["new_genre"] == [] and d["fills"] == {"TPE2": "Karan Aujla"}   # only the blank album artist is filled
    # preview wrote nothing
    assert {n: (read_state(x).genres, read_state(x).comments) for n, x in p.items()} == before

    assert client.post(f"/api/repair/{rid}/apply", json={}).status_code == 400
    r = client.post(f"/api/repair/{rid}/apply", json={"confirm": True}).json()
    assert r["results"] == {"done": 4}
    assert read_state(p["a"]).genres == ["Hip Hop"] and read_state(p["a"]).comments == []
    assert read_state(p["b"]).genres == [] and read_state(p["b"]).comments == []
    assert read_state(p["c"]).genres == ["Punjabi Pop"]
    assert read_state(p["d"]).comments == [("TXXX:comment", ["Live in Toronto"])] and read_state(p["d"]).albumartist == "Karan Aujla"

    assert client.post(f"/api/repair/{rid}/undo", json={"confirm": True}).json()["undone"] == 4
    assert {n: (read_state(x).genres, read_state(x).comments) for n, x in p.items()} == before
    assert all(read_state(x).albumartist == "" for x in p.values())      # filled album artists removed again


def test_without_itunes_only_junk_is_cleaned(client, make_mp3, tmp_path):
    lib, p = make_lib(make_mp3, tmp_path)
    rid = client.post("/api/repair", json={"folder": str(lib), "itunes": False}).json()["id"]
    s = wait_ready(client, rid)
    assert s["summary"]["genres_filled"] == 0 and s["summary"]["genres_overridden"] == 0
    assert s["summary"]["junk_comments"] == 2 and s["summary"]["junk_genres_cleared"] == 1


def test_validation_and_unknown_ids(client, tmp_path):
    assert client.post("/api/repair", json={"folder": str(tmp_path / "nope")}).status_code == 400
    assert client.get("/api/repair/missing").status_code == 404
    assert client.post("/api/repair/missing/apply", json={"confirm": True}).status_code == 404
    assert client.post("/api/repair", json={"folder": str(tmp_path)},
                       headers={"Origin": "https://evil.example.com"}).status_code == 403


def test_cannot_apply_twice_or_undo_before_applying(client, make_mp3, tmp_path):
    lib, _ = make_lib(make_mp3, tmp_path)
    rid = client.post("/api/repair", json={"folder": str(lib)}).json()["id"]
    wait_ready(client, rid)
    assert client.post(f"/api/repair/{rid}/undo", json={"confirm": True}).status_code == 409
    assert client.post(f"/api/repair/{rid}/apply", json={"confirm": True}).status_code == 200
    assert client.post(f"/api/repair/{rid}/apply", json={"confirm": True}).status_code == 409
