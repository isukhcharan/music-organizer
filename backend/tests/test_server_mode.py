import pytest
from fastapi.testclient import TestClient

from musicorganizer.api import create_app
from tests.test_api import Fake


def make_client(tmp_path, **kw):
    return TestClient(create_app(tmp_path / "s.db", provider_factory=lambda n: Fake(), trash=None, **kw))


def test_tailscale_hostname_is_accepted_only_when_configured(tmp_path):
    ts = "instance-1.tail1234.ts.net"
    plain = make_client(tmp_path)
    assert plain.get("/api/status", headers={"Host": ts}).status_code == 403
    configured = make_client(tmp_path, allowed_hosts={ts})
    assert configured.get("/api/status", headers={"Host": ts}).status_code == 200
    assert configured.get("/api/status", headers={"Host": ts, "Origin": f"https://{ts}"}).status_code == 200
    assert configured.get("/api/status", headers={"Host": ts, "Origin": "https://evil.example.com"}).status_code == 403
    assert configured.get("/api/status", headers={"Host": "other.ts.net"}).status_code == 403


def test_hosts_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_ALLOWED_HOSTS", "a.ts.net, B.ts.net")
    c = make_client(tmp_path)
    assert c.get("/api/status", headers={"Host": "a.ts.net"}).status_code == 200
    assert c.get("/api/status", headers={"Host": "b.ts.net"}).status_code == 200        # case-insensitive


@pytest.fixture
def rooted(tmp_path, make_mp3):
    music = tmp_path / "music"
    make_mp3("a.mp3", folder=music / "Album One", title="A", artist="X")
    (tmp_path / "secret").mkdir()
    make_mp3("s.mp3", folder=tmp_path / "secret", title="S")
    return make_client(tmp_path, roots=[str(music)]), music, tmp_path


def test_folder_browser_is_confined_to_the_roots(rooted):
    client, music, tmp = rooted
    top = client.get("/api/fs/list").json()
    assert [d["path"] for d in top["dirs"]] == [str(music.resolve())] and top["parent"] is None
    inside = client.get("/api/fs/list", params={"path": str(music)}).json()
    assert [d["name"] for d in inside["dirs"]] == ["Album One"] and inside["parent"] == ""     # "up" goes back to the roots
    sub = client.get("/api/fs/list", params={"path": str(music / "Album One")}).json()
    assert sub["parent"] == str(music) and sub["audio_files_here"] == 1
    assert client.get("/api/fs/list", params={"path": str(tmp)}).status_code == 403
    assert client.get("/api/fs/list", params={"path": str(tmp / "secret")}).status_code == 403
    assert client.get("/api/fs/list", params={"path": str(music / ".." / "secret")}).status_code == 403     # .. cannot escape


def test_jobs_and_repairs_must_stay_inside_the_roots(rooted):
    client, music, tmp = rooted
    ok = client.post("/api/jobs", json={"input_dir": str(music / "Album One"), "output_dir": str(music / "Sorted")})
    assert ok.status_code == 201
    for bad_in, bad_out in [(tmp / "secret", music / "x"), (music / "Album One", tmp / "elsewhere"),
                            (music / ".." / "secret", music / "x")]:
        r = client.post("/api/jobs", json={"input_dir": str(bad_in), "output_dir": str(bad_out)})
        assert r.status_code == 400 and "inside the music folder" in r.json()["detail"]
    assert client.post("/api/repair", json={"folder": str(tmp / "secret")}).status_code == 400
    assert client.post("/api/repair", json={"folder": str(music / "Album One"), "itunes": False}).status_code == 201


def test_native_dialog_is_disabled_on_a_server(rooted):
    client, *_ = rooted
    assert client.post("/api/fs/pick", json={}).status_code == 501
