import app.main as main_mod
from app.classify import Category
from app.config import Settings, get_settings
from app.downloader import DownloadResult
from app.main import app
from fastapi.testclient import TestClient


def _override_settings():
    return Settings(_env_file=None, auth_token="testtoken")


app.dependency_overrides[get_settings] = _override_settings
client = TestClient(app)
AUTH = {"Authorization": "Bearer testtoken"}


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_download_requires_token():
    r = client.post("/download", content="https://fb/v/1")
    assert r.status_code == 401


def test_download_no_url_returns_message():
    r = client.post("/download", content="just some text", headers=AUTH)
    assert r.status_code == 200
    assert "No link" in r.text


def test_download_success(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    cfg = Settings(_env_file=None, auth_token="testtoken", cookies_path=str(cookies))
    app.dependency_overrides[get_settings] = lambda: cfg
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, c: DownloadResult(Category.OK, "clip.mp4", "✅ Saved: clip.mp4", ""),
    )
    try:
        r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
        assert r.status_code == 200
        assert r.text == "✅ Saved: clip.mp4"
    finally:
        app.dependency_overrides[get_settings] = _override_settings


def test_download_failure_still_200_with_message(tmp_path, monkeypatch):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    cfg = Settings(_env_file=None, auth_token="testtoken", cookies_path=str(cookies))
    app.dependency_overrides[get_settings] = lambda: cfg
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, c: DownloadResult(Category.STALE_COOKIES, None, "🔑 Login expired — ask John to refresh cookies", ""),
    )
    try:
        r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
        assert r.status_code == 200
        assert "Login expired" in r.text
    finally:
        app.dependency_overrides[get_settings] = _override_settings
