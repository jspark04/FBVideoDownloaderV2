from types import SimpleNamespace

from app.classify import Category
from app.config import Settings
from app.downloader import DownloadResult, build_ytdlp_cmd, run_download


def _cfg():
    return Settings(_env_file=None, auth_token="x")


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_build_cmd_has_recipe():
    cmd = build_ytdlp_cmd("https://fb/v/1", _cfg())
    assert "yt-dlp" in cmd[0]
    assert "--cookies" in cmd and "/config/cookies.txt" in cmd
    assert "--impersonate" in cmd and "chrome" in cmd
    assert cmd[-1] == "https://fb/v/1"


def test_success_returns_saved_message():
    runner = lambda cmd: _proc(returncode=0, stdout="/downloads/My Clip [123].mp4\n")
    res = run_download("https://fb/v/1", _cfg(), runner=runner)
    assert res.category == Category.OK
    assert "My Clip [123].mp4" in res.message
    assert res.message.startswith("✅")


def test_failure_stale_cookies():
    runner = lambda cmd: _proc(returncode=1, stderr="ERROR: You must log in to continue. Use --cookies")
    res = run_download("https://fb/v/1", _cfg(), runner=runner)
    assert res.category == Category.STALE_COOKIES
    assert res.message.startswith("🔑")


def test_failure_extractor_broken():
    runner = lambda cmd: _proc(returncode=1, stderr="ERROR: Cannot parse data; please report")
    res = run_download("https://fb/v/1", _cfg(), runner=runner)
    assert res.category == Category.EXTRACTOR_BROKEN
    assert res.message.startswith("⚠️")
