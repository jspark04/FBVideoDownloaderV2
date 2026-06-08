from app.config import Settings


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret123")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/topic")
    s = Settings(_env_file=None)
    assert s.auth_token == "secret123"
    assert s.ntfy_url == "https://ntfy.sh/topic"
    assert s.cookies_path == "/config/cookies.txt"
    assert s.download_dir == "/downloads"
    assert s.impersonate_target == "chrome"
    assert s.port == 8080
    assert s.notify_on_success is True


def test_settings_missing_token_raises(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    import pytest
    with pytest.raises(Exception):
        Settings(_env_file=None)


def test_settings_empty_token_raises(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "")
    import pytest
    with pytest.raises(Exception):
        Settings(_env_file=None)
