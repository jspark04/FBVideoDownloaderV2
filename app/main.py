import asyncio
import contextlib
import html
import logging
import os
import secrets
import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse

from .classify import Category
from .config import Settings, get_settings
from .cookies import validate_netscape, write_cookies_atomic
from .downloader import run_download
from .notify import send_ntfy
from .probe import CookieStatus, probe_cookies
from .state import state
from .urls import extract_url

logger = logging.getLogger("fbdl")

PROBE_INTERVAL_SECONDS = 6 * 60 * 60  # every 6 hours


def _probe_once(settings) -> None:
    was_ok = state.cookie.ok
    status = probe_cookies(settings)
    state.set_cookie(status)
    # Alert only on a fresh transition into "stale" (avoid repeat spam).
    if status.ok is False and was_ok is not False:
        send_ntfy(
            settings,
            "🔑 Facebook login expired — open fbdl and upload fresh cookies",
            priority="high", tags=["key"],
        )


@contextlib.asynccontextmanager
async def _lifespan(app_):
    settings = get_settings()

    async def _loop():
        while True:
            with contextlib.suppress(Exception):
                await run_in_threadpool(_probe_once, settings)
            await asyncio.sleep(PROBE_INTERVAL_SECONDS)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="fbdl", lifespan=_lifespan)
_download_lock = threading.Lock()


def require_token(
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> None:
    if not secrets.compare_digest(authorization, f"Bearer {settings.auth_token}"):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/download", response_class=PlainTextResponse)
async def download(
    request: Request,
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_token),
) -> PlainTextResponse:
    raw = (await request.body()).decode("utf-8", "ignore").strip()
    url = extract_url(raw)
    if not url:
        return PlainTextResponse("❌ No link found in what you shared")

    # Fast-fail without a doomed download if we already know the login is dead.
    # Intentional: /download does NOT self-recover — the stale flag is cleared
    # only by POST /cookies or the scheduled probe. Do not "fix" this to retry.
    if state.cookie.ok is False:
        return PlainTextResponse("🔑 Login expired — ask John to refresh cookies")

    # No cookies on disk yet (first run, or file removed): there's no login to
    # use, so tell the user to upload rather than running a doomed download.
    if not os.path.exists(settings.cookies_path):
        msg = "🔑 No Facebook login yet — upload cookies at the status page"
        state.record_job(url, Category.STALE_COOKIES.value, msg)
        return PlainTextResponse(msg)

    def _job():
        with _download_lock:
            return run_download(url, settings)

    result = await run_in_threadpool(_job)
    state.record_job(url, result.category.value, result.message)

    if result.category == Category.OK:
        state.set_cookie(CookieStatus(True, "ok"))
        if settings.notify_on_success:
            send_ntfy(settings, result.message, tags=["white_check_mark"])
    else:
        if result.category == Category.STALE_COOKIES:
            state.set_cookie(CookieStatus(False, "cookies expired"))
        # Make "check the server logs" actually mean something: record the real
        # yt-dlp error (last 1000 chars) so `docker logs fbdl` shows the reason.
        logger.warning(
            "download failed [%s] %s\n%s",
            result.category.value, url, (result.raw or "")[-1000:],
        )
        # Mirror every failure to my phone for awareness.
        send_ntfy(settings, f"{result.message}\n{url}", priority="high", tags=["warning"])

    return PlainTextResponse(result.message)


@app.post("/cookies", response_class=PlainTextResponse)
async def upload_cookies(
    request: Request,
    settings: Settings = Depends(get_settings),
    _: None = Depends(require_token),
) -> PlainTextResponse:
    body = (await request.body()).decode("utf-8", "ignore")
    ok, reason = validate_netscape(body)
    if not ok:
        return PlainTextResponse(f"❌ Not a valid cookies.txt: {reason}")
    write_cookies_atomic(settings.cookies_path, body)
    try:
        status = await run_in_threadpool(lambda: probe_cookies(settings))
    except Exception as exc:  # never 500 after the file is already saved
        status = CookieStatus(None, f"probe error: {exc}")
    state.set_cookie(status)
    if status.ok:
        return PlainTextResponse("✅ Cookies saved and verified — you're good.")
    if status.ok is False:
        return PlainTextResponse("⚠️ Saved, but they still look expired. Re-export and try again.")
    return PlainTextResponse("✅ Cookies saved (couldn't fully verify — no canary set).")


@app.get("/", response_class=HTMLResponse)
def status_page() -> HTMLResponse:
    c = state.cookie
    badge = {True: "🟢 valid", False: "🔴 expired", None: "⚪ unknown"}[c.ok]
    jobs = list(state.jobs)
    rows = "".join(
        f"<tr><td>{html.escape(j.when)}</td><td>{html.escape(j.category)}</td><td>{html.escape(j.message)}</td></tr>"
        for j in jobs
    ) or "<tr><td colspan=3>no downloads yet</td></tr>"
    return HTMLResponse(f"""<!doctype html>
<html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>fbdl</title>
<style>body{{font-family:system-ui;margin:1.2rem;max-width:760px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.4rem;text-align:left;font-size:.9rem}}
.box{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}}</style></head>
<body>
<h1>fbdl — Facebook → NAS</h1>
<div class=box><b>Facebook login:</b> {badge} <small>({html.escape(c.detail)})</small></div>
<div class=box>
  <b>Download a video</b>
  <p>In Facebook, tap the video's <b>Copy link</b>, then paste it here:</p>
  <form id=dlf>
    <input type=text id=dlurl style="width:100%" placeholder="https://www.facebook.com/...">
    <br><br>
    <button type=submit>Download</button> <span id=dlmsg></span>
  </form>
</div>
<div class=box>
  <b>Refresh cookies</b>
  <p>Export <code>cookies.txt</code> from your browser, then drop its contents here:</p>
  <form id=f>
    <input type=file id=file accept=".txt"><br><br>
    <textarea id=txt rows=4 style="width:100%" placeholder="...or paste cookies.txt contents"></textarea><br>
    <button type=submit>Upload</button> <span id=msg></span>
  </form>
</div>
<div class=box><b>Recent downloads</b>
  <table><tr><th>When</th><th>Result</th><th>Message</th></tr>{rows}</table>
</div>
<script>
const tok = localStorage.getItem('fbdl_token') || prompt('Bearer token (saved locally):');
if (tok) localStorage.setItem('fbdl_token', tok);
f.onsubmit = async (e) => {{
  e.preventDefault();
  let body = txt.value;
  if (file.files[0]) body = await file.files[0].text();
  msg.textContent = 'uploading...';
  const r = await fetch('/cookies', {{method:'POST', headers:{{'Authorization':'Bearer '+tok}}, body}});
  msg.textContent = await r.text();
}};
dlf.onsubmit = async (e) => {{
  e.preventDefault();
  dlmsg.textContent = 'downloading… this can take a moment';
  try {{
    const r = await fetch('/download', {{method:'POST', headers:{{'Authorization':'Bearer '+tok}}, body: dlurl.value}});
    dlmsg.textContent = await r.text();
  }} catch (err) {{ dlmsg.textContent = '❌ ' + err; }}
}};
</script>
</body></html>""")
