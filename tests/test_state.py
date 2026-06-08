from app.probe import CookieStatus
from app.state import AppState


def test_records_jobs_newest_first_and_caps():
    s = AppState(max_jobs=2)
    s.record_job("u1", "ok", "✅ Saved: a")
    s.record_job("u2", "ok", "✅ Saved: b")
    s.record_job("u3", "ok", "✅ Saved: c")
    msgs = [j.message for j in s.jobs]
    assert msgs == ["✅ Saved: c", "✅ Saved: b"]  # newest first, max 2


def test_set_cookie_status():
    s = AppState()
    s.set_cookie(CookieStatus(False, "cookies expired"))
    assert s.cookie.ok is False
    assert s.cookie.detail == "cookies expired"
