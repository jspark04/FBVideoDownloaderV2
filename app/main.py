import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from .config import Settings, get_settings
from .downloader import run_download
from .urls import extract_url

app = FastAPI(title="fbdl")
_download_lock = threading.Lock()


def require_token(
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> None:
    if authorization != f"Bearer {settings.auth_token}":
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
        # Always 200 so the phone shortcut reliably displays our message.
        return PlainTextResponse("❌ No link found in what you shared")

    def _job():
        with _download_lock:
            return run_download(url, settings)

    result = await run_in_threadpool(_job)
    return PlainTextResponse(result.message)
