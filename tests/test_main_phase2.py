import pytest
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


@pytest.fixture(autouse=True)
def _reset_state():
    main_mod.state.set_cookie(CookieStatus(None, "reset"))
    main_mod.state.jobs.clear()
    yield


def test_fast_fail_when_cookies_known_stale(monkeypatch):
    main_mod.state.set_cookie(CookieStatus(False, "cookies expired"))
    def _boom(url, cfg):
        raise AssertionError("should not download when cookies known stale")
    monkeypatch.setattr(main_mod, "run_download", _boom)
    r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
    assert r.status_code == 200
    assert "Login expired" in r.text
    main_mod.state.set_cookie(CookieStatus(None, "reset"))  # cleanup


def test_download_records_job_and_sets_cookie_ok(tmp_path, monkeypatch):
    main_mod.state.set_cookie(CookieStatus(None, "reset"))
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    cfg = Settings(_env_file=None, auth_token="testtoken", ntfy_url="", cookies_path=str(cookies))
    main_mod.app.dependency_overrides[get_settings] = lambda: cfg
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, c: DownloadResult(Category.OK, "clip.mp4", "✅ Saved: clip.mp4", ""),
    )
    monkeypatch.setattr(main_mod, "send_ntfy", lambda *a, **k: True)
    try:
        r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
        assert r.text == "✅ Saved: clip.mp4"
        assert main_mod.state.cookie.ok is True
        assert any("clip.mp4" in j.message for j in main_mod.state.jobs)
    finally:
        main_mod.app.dependency_overrides[get_settings] = _override_settings


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


def test_status_page_has_download_box():
    # Phones can't "Share to other apps" reliably, so the page must offer a
    # paste-a-link download box (Copy link in FB -> paste here -> Download).
    r = client.get("/")
    assert r.status_code == 200
    assert "download a video" in r.text.lower()  # the box heading
    assert "dlurl" in r.text                       # the paste input
    assert "/download" in r.text                   # JS posts to the download endpoint


def test_status_page_escapes_job_message():
    main_mod.state.record_job("https://fb/v/1", "ok", "✅ Saved: <script>alert(1)</script>.mp4")
    r = client.get("/")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_probe_once_alerts_on_transition_to_stale(monkeypatch):
    import app.main as m
    from app.config import Settings
    from app.probe import CookieStatus
    m.state.set_cookie(CookieStatus(True, "ok"))
    monkeypatch.setattr(m, "probe_cookies", lambda cfg: CookieStatus(False, "cookies expired"))
    sent = {}
    monkeypatch.setattr(m, "send_ntfy", lambda settings, msg, **k: sent.setdefault("msg", msg) or True)
    cfg = Settings(_env_file=None, auth_token="x", ntfy_url="https://ntfy.sh/t")
    m._probe_once(cfg)
    assert m.state.cookie.ok is False
    assert "expired" in sent["msg"].lower()


def test_download_missing_cookies_file_prompts_upload(tmp_path, monkeypatch):
    # No cookies.txt on disk yet (first run): the user should be told to upload
    # cookies, NOT see a generic failure — and we must not attempt a doomed download.
    cfg = Settings(_env_file=None, auth_token="testtoken", cookies_path=str(tmp_path / "nope.txt"))
    main_mod.app.dependency_overrides[get_settings] = lambda: cfg

    def _boom(url, c):
        raise AssertionError("must not attempt download when cookies file is missing")

    monkeypatch.setattr(main_mod, "run_download", _boom)
    try:
        r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
        assert r.status_code == 200
        assert "🔑" in r.text
        assert "upload" in r.text.lower()
    finally:
        main_mod.app.dependency_overrides[get_settings] = _override_settings  # restore
