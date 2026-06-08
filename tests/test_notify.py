from app.config import Settings
from app.notify import build_ntfy_request, send_ntfy


def test_build_request_headers_and_body():
    url, headers, data = build_ntfy_request(
        "https://ntfy.sh/topic", "hello", title="FB Downloader",
        priority="high", tags=["warning"],
    )
    assert url == "https://ntfy.sh/topic"
    assert headers["Title"] == "FB Downloader"
    assert headers["Priority"] == "high"
    assert headers["Tags"] == "warning"
    assert data == b"hello"


def test_send_noop_when_no_url():
    cfg = Settings(_env_file=None, auth_token="x", ntfy_url="")
    assert send_ntfy(cfg, "hi") is False


def test_send_posts_with_client():
    cfg = Settings(_env_file=None, auth_token="x", ntfy_url="https://ntfy.sh/topic")
    calls = {}

    class FakeResp:
        status_code = 200

    class FakeClient:
        def post(self, url, headers=None, content=None):
            calls["url"] = url
            calls["content"] = content
            return FakeResp()

    assert send_ntfy(cfg, "hi", client=FakeClient()) is True
    assert calls["url"] == "https://ntfy.sh/topic"
    assert calls["content"] == b"hi"
