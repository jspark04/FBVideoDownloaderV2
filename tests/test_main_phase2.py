import app.main as main_mod
from app.classify import Category
from app.config import Settings, get_settings
from app.downloader import DownloadResult
from app.main import app
from app.probe import CookieStatus
from fastapi.testclient import TestClient


def _override_settings():
    return Settings(_env_file=None, auth_token="testtoken", ntfy_url="")


app.dependency_overrides[get_settings] = _override_settings
client = TestClient(app)
AUTH = {"Authorization": "Bearer testtoken"}

GOOD_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".facebook.com\tTRUE\t/\tTRUE\t1999999999\tc_user\t123\n"
)


def test_fast_fail_when_cookies_known_stale(monkeypatch):
    main_mod.state.set_cookie(CookieStatus(False, "cookies expired"))
    def _boom(url, cfg):
        raise AssertionError("should not download when cookies known stale")
    monkeypatch.setattr(main_mod, "run_download", _boom)
    r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
    assert r.status_code == 200
    assert "Login expired" in r.text
    main_mod.state.set_cookie(CookieStatus(None, "reset"))  # cleanup


def test_download_records_job_and_sets_cookie_ok(monkeypatch):
    main_mod.state.set_cookie(CookieStatus(None, "reset"))
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, cfg: DownloadResult(Category.OK, "clip.mp4", "✅ Saved: clip.mp4", ""),
    )
    monkeypatch.setattr(main_mod, "send_ntfy", lambda *a, **k: True)
    r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
    assert r.text == "✅ Saved: clip.mp4"
    assert main_mod.state.cookie.ok is True
    assert any("clip.mp4" in j.message for j in main_mod.state.jobs)


def test_cookies_upload_rejects_bad_format():
    r = client.post("/cookies", content="not a cookie file", headers=AUTH)
    assert r.status_code == 200
    assert "❌" in r.text or "missing" in r.text.lower()


def test_cookies_upload_accepts_good(tmp_path, monkeypatch):
    cfg = Settings(_env_file=None, auth_token="testtoken", cookies_path=str(tmp_path / "cookies.txt"))
    main_mod.app.dependency_overrides[get_settings] = lambda: cfg
    monkeypatch.setattr(main_mod, "probe_cookies", lambda c: CookieStatus(True, "ok"))
    r = client.post("/cookies", content=GOOD_COOKIES, headers=AUTH)
    assert r.status_code == 200
    assert "✅" in r.text
    assert (tmp_path / "cookies.txt").read_text() == GOOD_COOKIES
    main_mod.app.dependency_overrides[get_settings] = _override_settings  # restore


def test_status_page_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "fbdl" in r.text.lower() or "cookie" in r.text.lower()
