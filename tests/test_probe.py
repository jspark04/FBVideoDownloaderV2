from types import SimpleNamespace

from app.config import Settings
from app.probe import CookieStatus, probe_cookies


def _cfg(canary="https://fb/v/1"):
    return Settings(_env_file=None, auth_token="x", canary_url=canary)


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_no_canary_is_unknown():
    st = probe_cookies(_cfg(canary=""))
    assert st.ok is None


def test_healthy_when_metadata_returns():
    st = probe_cookies(_cfg(), runner=lambda cfg: _proc(returncode=0, stdout='{"id":"1"}'))
    assert st.ok is True


def test_stale_when_login_required():
    runner = lambda cfg: _proc(returncode=1, stderr="ERROR: You must log in to continue. Use --cookies")
    st = probe_cookies(_cfg(), runner=runner)
    assert st.ok is False


def test_extractor_break_is_inconclusive_not_stale():
    runner = lambda cfg: _proc(returncode=1, stderr="ERROR: Cannot parse data; please report")
    st = probe_cookies(_cfg(), runner=runner)
    assert st.ok is None
