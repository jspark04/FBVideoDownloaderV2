import httpx


def build_ntfy_request(
    ntfy_url: str, message: str, title: str = "FB Downloader",
    priority: str = "default", tags=None,
):
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    return ntfy_url, headers, message.encode("utf-8")


def send_ntfy(
    cfg, message: str, title: str = "FB Downloader",
    priority: str = "default", tags=None, client=None,
) -> bool:
    """POST a push to ntfy. Returns True on success, False on no-url/failure."""
    if not cfg.ntfy_url:
        return False
    url, headers, data = build_ntfy_request(cfg.ntfy_url, message, title, priority, tags)
    owns_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        resp = client.post(url, headers=headers, content=data)
        return resp.status_code < 400
    except Exception:
        return False
    finally:
        if owns_client:
            client.close()
