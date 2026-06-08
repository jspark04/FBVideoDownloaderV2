# Facebook Video Downloader — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single Docker container on a Synology NAS that accepts a Facebook video URL (shared from an Android phone), downloads it with yt-dlp, saves it to a NAS folder, and confirms success/failure back to whoever triggered it.

**Architecture:** A small FastAPI service. `POST /download` runs **synchronously** under a single-flight lock so the caller's HTTP response *is* the confirmation (`✅ Saved` / `❌ Failed: reason` / `🔑 Login expired`). yt-dlp is driven with cookies + `--impersonate chrome` + curl_cffi (the confirmed recipe for private-group video). Phase 1 delivers the phone→NAS core; Phase 2 adds cookie-upload UI, stale-detection, ntfy alerts, and a status page. Reachability (Tailscale) and the phone shortcut are configured outside the code (runbook in the spec, §9).

**Tech Stack:** Python 3.12, FastAPI + uvicorn, yt-dlp[default,curl-cffi], ffmpeg (bundled), httpx, pydantic-settings, pytest. No database (small in-memory state). Reference spec: [docs/superpowers/specs/2026-06-07-fb-video-downloader-design.md](../specs/2026-06-07-fb-video-downloader-design.md).

---

## File Structure

```
FBVideoDownloaderV2/
├── requirements.txt            # runtime deps
├── requirements-dev.txt        # runtime + pytest
├── Dockerfile                  # python:3.12-slim + ffmpeg + app
├── docker-compose.yml          # Container Manager project (env_file + volumes)
├── .env.example                # config template (committed); real .env is git-ignored
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── config.py               # Settings (env vars via pydantic-settings)
│   ├── urls.py                 # extract_url(text) -> str | None         (pure)
│   ├── classify.py             # Category enum + classify(error) -> Category (pure)
│   ├── downloader.py           # build_ytdlp_cmd() + run_download()       (subprocess, injectable runner)
│   ├── notify.py               # build_ntfy_request() (pure) + send_ntfy()   [Phase 2]
│   ├── probe.py                # CookieStatus + probe_cookies()           [Phase 2]
│   ├── cookies.py              # validate_netscape() + write_cookies_atomic() [Phase 2]
│   ├── state.py                # AppState (cookie status + recent jobs)   [Phase 2]
│   └── main.py                 # FastAPI app, routes, auth, lock, lifespan
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_urls.py
    ├── test_classify.py
    ├── test_downloader.py
    ├── test_main.py
    ├── test_cookies.py         [Phase 2]
    ├── test_notify.py          [Phase 2]
    ├── test_probe.py           [Phase 2]
    └── test_main_phase2.py     [Phase 2]
```

**Design notes:**
- **Pure functions are isolated and unit-tested** (`extract_url`, `classify`, `validate_netscape`, `build_ytdlp_cmd`, `build_ntfy_request`). The IO wrappers (`run_download`, `send_ntfy`, `probe_cookies`) take an **injectable runner/client** so tests never touch the network, subprocess, or yt-dlp.
- `main.py` stays thin: parse request → call a function → return its message.
- Each file has one responsibility; none should need to be held in context with the others to be understood.

**Conventions for every task below:** run `pytest` from the repo root. The container runs as `app.main:app`. Commit after each task with the message shown.

---

# PHASE 1 — Phone → NAS core (MVP)

At the end of Phase 1: sharing a Facebook URL to the container downloads the video to the NAS folder and returns a success/failure message. Cookies are supplied by dropping `cookies.txt` into the `config/` folder manually (the upload UI comes in Phase 2).

---

### Task 1: Repo scaffolding & dependencies

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `app/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
.env
config/
downloads/
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
```

- [ ] **Step 2: Create `requirements.txt`**

```text
fastapi
uvicorn[standard]
yt-dlp[default,curl-cffi]
httpx
pydantic-settings
python-multipart
```

- [ ] **Step 3: Create `requirements-dev.txt`**

```text
-r requirements.txt
pytest
```

- [ ] **Step 4: Create empty package markers**

Create `app/__init__.py` and `tests/__init__.py` as empty files.

- [ ] **Step 5: Create `tests/test_smoke.py` (proves pytest runs)**

```python
def test_smoke():
    assert True
```

- [ ] **Step 6: Set up the dev environment and run the smoke test**

Run:
```bash
python -m venv .venv
. .venv/Scripts/activate    # Windows; on Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```
Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add .gitignore requirements.txt requirements-dev.txt app tests
git commit -m "chore: scaffold project (deps, package layout, smoke test)"
```

---

### Task 2: Configuration (`app/config.py`)

**Files:**
- Create: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from app.config import Settings


def test_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret123")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/topic")
    s = Settings(_env_file=None)
    assert s.auth_token == "secret123"
    assert s.ntfy_url == "https://ntfy.sh/topic"
    # defaults
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_token: str                                  # required
    cookies_path: str = "/config/cookies.txt"
    download_dir: str = "/downloads"
    impersonate_target: str = "chrome"
    ntfy_url: str = ""
    canary_url: str = ""
    notify_on_success: bool = True
    port: int = 8080


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat: env-based Settings (config.py)"
```

---

### Task 3: URL extraction (`app/urls.py`)

**Files:**
- Create: `app/urls.py`
- Test: `tests/test_urls.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_urls.py
from app.urls import extract_url


def test_plain_url():
    assert extract_url("https://www.facebook.com/watch/?v=123") == "https://www.facebook.com/watch/?v=123"


def test_url_with_surrounding_text():
    text = "Check this out! https://fb.watch/abcd/ so cute"
    assert extract_url(text) == "https://fb.watch/abcd/"


def test_strips_trailing_punctuation():
    assert extract_url("see https://www.facebook.com/reel/999).") == "https://www.facebook.com/reel/999"


def test_no_url_returns_none():
    assert extract_url("no link here") is None


def test_empty_returns_none():
    assert extract_url("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_urls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.urls'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/urls.py
import re

_URL_RE = re.compile(r"https?://[^\s]+")
_TRAILING = ").,]>\"'"


def extract_url(text: str) -> str | None:
    """Return the first http(s) URL found in arbitrary shared text, or None.

    Facebook's share sheet often sends the URL embedded in title text, so we
    scan for the first URL rather than assuming the whole body is the URL.
    """
    if not text:
        return None
    match = _URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(_TRAILING)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_urls.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/urls.py tests/test_urls.py
git commit -m "feat: extract_url() from shared text (urls.py)"
```

---

### Task 4: Error classifier (`app/classify.py`)

This is the highest-value unit: it turns a yt-dlp failure into an actionable category. Fixtures are real error strings from the research.

**Files:**
- Create: `app/classify.py`
- Test: `tests/test_classify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify.py
from app.classify import Category, classify


def test_stale_cookies_login_required():
    err = ("ERROR: [facebook] 123: Cannot find webpage. You must log in to continue. "
           "Use --cookies, --username and --password or --netrc to provide account credentials")
    assert classify(err) == Category.STALE_COOKIES


def test_extractor_broken_cannot_parse():
    err = ("ERROR: [facebook] 456: Cannot parse data; please report this issue "
           "on https://github.com/yt-dlp/yt-dlp/issues")
    assert classify(err) == Category.EXTRACTOR_BROKEN


def test_video_unsupported_not_available():
    err = 'ERROR: [facebook] 789: The video is not available, Facebook said: PME:1000'
    assert classify(err) == Category.VIDEO_UNSUPPORTED


def test_video_unsupported_url():
    err = "ERROR: Unsupported URL: https://www.facebook.com/groups/123/"
    assert classify(err) == Category.VIDEO_UNSUPPORTED


def test_other_for_unknown():
    assert classify("ERROR: HTTP Error 500: Internal Server Error") == Category.OTHER


def test_empty_is_other():
    assert classify("") == Category.OTHER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.classify'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/classify.py
from enum import Enum


class Category(str, Enum):
    OK = "ok"
    STALE_COOKIES = "stale_cookies"
    EXTRACTOR_BROKEN = "extractor_broken"
    VIDEO_UNSUPPORTED = "video_unsupported"
    OTHER = "other"


_STALE = ("use --cookies", "log in to continue", "login_form", "loginbutton")
_BROKEN = ("cannot parse data",)
_UNSUPPORTED = ("is not available", "only available for registered users", "unsupported url")


def classify(error_text: str) -> Category:
    """Map a yt-dlp error string to an actionable category.

    Order matters: a login wall is checked before the generic parse error.
    """
    text = (error_text or "").lower()
    if any(s in text for s in _STALE):
        return Category.STALE_COOKIES
    if any(s in text for s in _BROKEN):
        return Category.EXTRACTOR_BROKEN
    if any(s in text for s in _UNSUPPORTED):
        return Category.VIDEO_UNSUPPORTED
    return Category.OTHER
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_classify.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/classify.py tests/test_classify.py
git commit -m "feat: classify yt-dlp errors into actionable categories (classify.py)"
```

---

### Task 5: Downloader (`app/downloader.py`)

Builds the yt-dlp command and runs it. The runner is injectable so tests never invoke yt-dlp.

**Files:**
- Create: `app/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_downloader.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_downloader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.downloader'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/downloader.py
import os
import subprocess
from dataclasses import dataclass

from .classify import Category, classify


@dataclass
class DownloadResult:
    category: Category
    title: str | None
    message: str   # short, friendly text shown on the phone
    raw: str       # raw stdout/stderr tail for logs / ntfy


_PHONE_MESSAGE = {
    Category.STALE_COOKIES: "🔑 Login expired — ask John to refresh cookies",
    Category.EXTRACTOR_BROKEN: "⚠️ Download broke (Facebook changed) — John needs to update",
    Category.VIDEO_UNSUPPORTED: "🚫 Couldn't download — try the video's own 'Copy link'",
    Category.OTHER: "❌ Failed — check the server logs",
}


def build_ytdlp_cmd(url: str, cfg) -> list[str]:
    """The confirmed private-group recipe: cookies + impersonate + merge to mp4.

    `--no-simulate --print after_move:filepath` makes yt-dlp download AND print
    the final saved path on stdout, which we use for the success message.
    """
    return [
        "yt-dlp",
        "--cookies", cfg.cookies_path,
        "--impersonate", cfg.impersonate_target,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-simulate",
        "--print", "after_move:filepath",
        "-o", "%(title)s [%(id)s].%(ext)s",
        "--paths", cfg.download_dir,
        url,
    ]


def _default_runner(cmd: list[str]):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def run_download(url: str, cfg, runner=None) -> DownloadResult:
    runner = runner or _default_runner
    cmd = build_ytdlp_cmd(url, cfg)
    proc = runner(cmd)
    if proc.returncode == 0:
        last = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        path = last[-1].strip() if last else ""
        name = os.path.basename(path) if path else "video"
        return DownloadResult(Category.OK, name, f"✅ Saved: {name}", proc.stdout or "")
    raw = proc.stderr or proc.stdout or ""
    cat = classify(raw)
    return DownloadResult(cat, None, _PHONE_MESSAGE[cat], raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_downloader.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/downloader.py tests/test_downloader.py
git commit -m "feat: yt-dlp command builder + run_download with injectable runner (downloader.py)"
```

---

### Task 6: FastAPI app — `/health` and `/download` (`app/main.py`)

**Files:**
- Create: `app/main.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main.py
import app.main as main_mod
from app.classify import Category
from app.config import Settings, get_settings
from app.downloader import DownloadResult
from app.main import app
from fastapi.testclient import TestClient


def _override_settings():
    return Settings(_env_file=None, auth_token="testtoken")


app.dependency_overrides[get_settings] = _override_settings
client = TestClient(app)
AUTH = {"Authorization": "Bearer testtoken"}


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_download_requires_token():
    r = client.post("/download", content="https://fb/v/1")
    assert r.status_code == 401


def test_download_no_url_returns_message():
    r = client.post("/download", content="just some text", headers=AUTH)
    assert r.status_code == 200
    assert "No link" in r.text


def test_download_success(monkeypatch):
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, cfg: DownloadResult(Category.OK, "clip.mp4", "✅ Saved: clip.mp4", ""),
    )
    r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
    assert r.status_code == 200
    assert r.text == "✅ Saved: clip.mp4"


def test_download_failure_still_200_with_message(monkeypatch):
    monkeypatch.setattr(
        main_mod, "run_download",
        lambda url, cfg: DownloadResult(Category.STALE_COOKIES, None, "🔑 Login expired — ask John to refresh cookies", ""),
    )
    r = client.post("/download", content="https://www.facebook.com/watch/?v=1", headers=AUTH)
    assert r.status_code == 200
    assert "Login expired" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/main.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: all green (smoke + config + urls + classify + downloader + main).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: FastAPI /health + synchronous /download with bearer auth and single-flight lock"
```

---

### Task 7: Containerization (Dockerfile, compose, .env.example)

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

# ffmpeg merges Facebook's separate video+audio streams (yt-dlp needs it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  fbdl:
    build: .
    container_name: fbdl
    env_file: .env
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config         # holds cookies.txt (and Phase 2 state)
      - ./downloads:/downloads   # finished videos land here (the deliverable)
    restart: unless-stopped
```

- [ ] **Step 3: Create `.env.example`**

```text
# Copy this file to ".env" and fill in the values. ".env" is git-ignored.

# Shared secret the phone shortcut sends as "Authorization: Bearer <token>".
# Generate one with:  openssl rand -hex 32
AUTH_TOKEN=replace-with-a-long-random-secret

# ntfy push (Phase 2). Pick an unguessable topic, install the ntfy Android app,
# and subscribe to this exact topic. Leave blank to disable notifications.
NTFY_URL=https://ntfy.sh/fbdl-change-me-7f3a9c2e

# A Facebook video URL that requires login, used to probe cookie validity (Phase 2).
CANARY_URL=

# Defaults below are fine for the standard container layout.
IMPERSONATE_TARGET=chrome
NOTIFY_ON_SUCCESS=true
COOKIES_PATH=/config/cookies.txt
DOWNLOAD_DIR=/downloads
PORT=8080
```

- [ ] **Step 4: Build the image locally to verify it builds**

Run:
```bash
docker build -t fbdl:dev .
```
Expected: build succeeds; final line shows the image tagged `fbdl:dev`. (If Docker isn't available locally, this step is performed on the Synology in Task 8.)

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example
git commit -m "build: Dockerfile (+ ffmpeg), compose project, and .env.example"
```

---

### Task 8: Phase 1 manual verification (no code — runbook)

This proves the core works end-to-end before adding robustness features. Perform on the Synology (or any Docker host with the real cookies).

- [ ] **Step 1: Prepare config**
  - Copy `.env.example` → `.env`; set `AUTH_TOKEN` (`openssl rand -hex 32`).
  - Create `./config` and `./downloads` folders next to the compose file.
  - Export a `cookies.txt` from a browser logged into Facebook (extension "Get cookies.txt LOCALLY" / "cookies.txt"); place it at `./config/cookies.txt`.

- [ ] **Step 2: Start the container**

Run: `docker compose up -d --build` (or import the project in Synology Container Manager).
Then: `curl http://localhost:8080/health` → expect `{"status":"ok"}`.

- [ ] **Step 3: Verify impersonation is actually available inside the container**

Run: `docker exec fbdl yt-dlp --list-impersonate-targets`
Expected: a list of targets that does **not** say "(unavailable)". (If unavailable, curl_cffi didn't install — revisit `requirements.txt`.)

- [ ] **Step 4: Trigger a real download**

Run (replace with a real group video URL you can view):
```bash
curl -X POST http://localhost:8080/download \
  -H "Authorization: Bearer <your-token>" \
  --data "https://www.facebook.com/groups/<g>/posts/<id>/"
```
Expected: response body `✅ Saved: <filename>.mp4`, and the file present in `./downloads`.

- [ ] **Step 5: Verify failure messaging**
  - Temporarily rename `config/cookies.txt`, repeat Step 4 → expect `🔑 Login expired ...`. Restore the file.

- [ ] **Step 6: Record the result**

If all pass, Phase 1 is done. Note the working canary URL (a login-required video) for `CANARY_URL` in Phase 2. No commit needed (verification only); if you tweaked anything, commit it.

---

# PHASE 2 — Robustness (cookie UI, stale-detection, ntfy, status page)

At the end of Phase 2: the system pings your phone when cookies go stale, lets you re-upload them via a web page (drag/drop), shows recent jobs, and fast-fails instantly when the login is already known dead.

---

### Task 9: Cookie validation & atomic write (`app/cookies.py`)

**Files:**
- Create: `app/cookies.py`
- Test: `tests/test_cookies.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cookies.py
from app.cookies import validate_netscape, write_cookies_atomic

GOOD = (
    "# Netscape HTTP Cookie File\n"
    ".facebook.com\tTRUE\t/\tTRUE\t1999999999\tc_user\t123\n"
    "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1999999999\txs\tabc\n"
)


def test_valid_cookies_ok():
    ok, reason = validate_netscape(GOOD)
    assert ok, reason


def test_missing_header_rejected():
    ok, reason = validate_netscape(".facebook.com\tTRUE\t/\tTRUE\t1\tc_user\t123\n")
    assert not ok
    assert "header" in reason.lower()


def test_wrong_field_count_rejected():
    bad = "# Netscape HTTP Cookie File\n.facebook.com TRUE / TRUE 1 c_user 123\n"  # spaces, not tabs
    ok, reason = validate_netscape(bad)
    assert not ok


def test_empty_rejected():
    ok, _ = validate_netscape("   ")
    assert not ok


def test_atomic_write_replaces(tmp_path):
    target = tmp_path / "cookies.txt"
    target.write_text("old")
    write_cookies_atomic(str(target), GOOD)
    assert target.read_text() == GOOD
    # no leftover temp files in the directory
    assert [p.name for p in tmp_path.iterdir()] == ["cookies.txt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cookies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.cookies'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/cookies.py
import os
import tempfile

_HEADERS = ("# HTTP Cookie File", "# Netscape HTTP Cookie File")
_HTTPONLY = "#HttpOnly_"


def validate_netscape(text: str) -> tuple[bool, str]:
    """Cheap structural check that `text` is a yt-dlp-loadable cookies.txt."""
    if not text or not text.strip():
        return False, "empty file"
    lines = text.splitlines()
    if not lines[0].strip().startswith(_HEADERS):
        return False, "missing '# Netscape HTTP Cookie File' header line"
    data = [
        ln for ln in lines
        if ln.strip() and (ln.startswith(_HTTPONLY) or not ln.startswith("#"))
    ]
    if not data:
        return False, "no cookie entries"
    for ln in data:
        core = ln[len(_HTTPONLY):] if ln.startswith(_HTTPONLY) else ln
        if len(core.split("\t")) != 7:
            return False, "expected 7 tab-separated fields per cookie line"
    return True, "ok"


def write_cookies_atomic(path: str, content: str) -> None:
    """Write cookies so a concurrent reader never sees a half file."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cookies.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cookies.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/cookies.py tests/test_cookies.py
git commit -m "feat: Netscape cookies validation + atomic write (cookies.py)"
```

---

### Task 10: ntfy notifications (`app/notify.py`)

**Files:**
- Create: `app/notify.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notify.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.notify'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/notify.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notify.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/notify.py tests/test_notify.py
git commit -m "feat: ntfy push (build_ntfy_request + send_ntfy) (notify.py)"
```

---

### Task 11: Cookie probe (`app/probe.py`)

**Files:**
- Create: `app/probe.py`
- Test: `tests/test_probe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_probe.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.probe'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/probe.py
import subprocess
from dataclasses import dataclass

from .classify import Category, classify


@dataclass
class CookieStatus:
    ok: bool | None = None       # True=valid, False=stale, None=unknown/inconclusive
    detail: str = "not checked yet"


def _default_probe_runner(cfg):
    cmd = [
        "yt-dlp", "-J", "--no-warnings", "--no-playlist",
        "--cookies", cfg.cookies_path,
        "--impersonate", cfg.impersonate_target,
        cfg.canary_url,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def probe_cookies(cfg, runner=None) -> CookieStatus:
    """Metadata-only check of whether the cookies still authenticate.

    Distinguishes 'cookies stale' from 'extractor broke' so we never tell the
    user to refresh cookies when the real problem is Facebook changing its API.
    """
    if not cfg.canary_url:
        return CookieStatus(None, "no canary url configured")
    runner = runner or _default_probe_runner
    proc = runner(cfg)
    if proc.returncode == 0:
        return CookieStatus(True, "ok")
    cat = classify(proc.stderr or proc.stdout)
    if cat == Category.STALE_COOKIES:
        return CookieStatus(False, "cookies expired")
    return CookieStatus(None, f"inconclusive ({cat.value})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_probe.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/probe.py tests/test_probe.py
git commit -m "feat: cookie staleness probe distinguishing stale vs extractor break (probe.py)"
```

---

### Task 12: App state (`app/state.py`)

**Files:**
- Create: `app/state.py`
- Test: extend `tests/test_probe.py` is not appropriate; add `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/state.py
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from .probe import CookieStatus


@dataclass
class JobRecord:
    when: str
    url: str
    category: str
    message: str


class AppState:
    def __init__(self, max_jobs: int = 20):
        self.cookie = CookieStatus()
        self.jobs: deque[JobRecord] = deque(maxlen=max_jobs)
        self._lock = Lock()

    def record_job(self, url: str, category: str, message: str) -> None:
        rec = JobRecord(datetime.now().strftime("%Y-%m-%d %H:%M"), url, category, message)
        with self._lock:
            self.jobs.appendleft(rec)

    def set_cookie(self, status: CookieStatus) -> None:
        with self._lock:
            self.cookie = status


state = AppState()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_state.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/state.py tests/test_state.py
git commit -m "feat: in-memory app state (cookie status + recent jobs) (state.py)"
```

---

### Task 13: Wire state, notifications, fast-fail, `/cookies` upload, status page into `main.py`

This task modifies `main.py` to use the Phase 2 modules. Replace the whole file with the version below (it is a superset of the Phase 1 file).

**Files:**
- Modify: `app/main.py` (full replacement)
- Test: `tests/test_main_phase2.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_phase2.py
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
    # run_download must NOT be called on fast-fail
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
    # probe says healthy after upload
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_phase2.py -v`
Expected: FAIL (e.g. `AttributeError: module 'app.main' has no attribute 'state'`, or 404 on `/cookies`).

- [ ] **Step 3: Replace `app/main.py` with the full Phase 2 version**

```python
# app/main.py
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
        return PlainTextResponse("❌ No link found in what you shared")

    # Fast-fail without a doomed download if we already know the login is dead.
    if state.cookie.ok is False:
        return PlainTextResponse("🔑 Login expired — ask John to refresh cookies")

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
    status = await run_in_threadpool(lambda: probe_cookies(settings))
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
    rows = "".join(
        f"<tr><td>{j.when}</td><td>{j.category}</td><td>{j.message}</td></tr>"
        for j in state.jobs
    ) or "<tr><td colspan=3>no downloads yet</td></tr>"
    return HTMLResponse(f"""<!doctype html>
<html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>fbdl</title>
<style>body{{font-family:system-ui;margin:1.2rem;max-width:760px}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:.4rem;text-align:left;font-size:.9rem}}
.box{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}}</style></head>
<body>
<h1>fbdl — Facebook → NAS</h1>
<div class=box><b>Facebook login:</b> {badge} <small>({c.detail})</small></div>
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
</script>
</body></html>""")
```

- [ ] **Step 4: Run the Phase 2 tests**

Run: `pytest tests/test_main_phase2.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main_phase2.py
git commit -m "feat: wire state, ntfy, fast-fail, /cookies upload, and status page into main"
```

---

### Task 14: Scheduled stale-cookie probe (background task in `main.py`)

Runs the probe periodically; on a transition to stale, pushes one ntfy alert so you can refresh *before* anyone hits a failure.

**Files:**
- Modify: `app/main.py` (add a lifespan task; add `PROBE_INTERVAL_SECONDS`)
- Test: `tests/test_main_phase2.py` (add one test)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_main_phase2.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_phase2.py::test_probe_once_alerts_on_transition_to_stale -v`
Expected: FAIL with `AttributeError: module 'app.main' has no attribute '_probe_once'`

- [ ] **Step 3: Add the probe logic + lifespan loop to `app/main.py`**

Add these imports at the top of `app/main.py` (alongside the existing imports):

```python
import asyncio
import contextlib
```

Add this function and lifespan wiring (place the function above `app = FastAPI(...)`, then replace the `app = FastAPI(title="fbdl")` line with the lifespan-enabled version):

```python
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
```

Then change the app construction line to:

```python
app = FastAPI(title="fbdl", lifespan=_lifespan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_phase2.py::test_probe_once_alerts_on_transition_to_stale -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main_phase2.py
git commit -m "feat: scheduled cookie probe with transition-to-stale ntfy alert"
```

---

### Task 15: README + Phase 2 verification

**Files:**
- Create: `README.md` (replace the placeholder)
- Verification (no code)

- [ ] **Step 1: Replace `README.md`**

```markdown
# FBVideoDownloaderV2

One small container that downloads a Facebook (private-group) video to a NAS folder
when you share its link from your phone. Reachable over Tailscale; confirms success/
failure back to whoever triggered it; pings you (ntfy) when the Facebook login needs
refreshing.

- Design: `docs/superpowers/specs/2026-06-07-fb-video-downloader-design.md`
- Plan: `docs/superpowers/plans/2026-06-07-fb-video-downloader.md`

## Run
1. `cp .env.example .env` and fill in `AUTH_TOKEN`, `NTFY_URL`, `CANARY_URL`.
2. Put a Facebook `cookies.txt` at `./config/cookies.txt` (or upload via the status page).
3. `docker compose up -d --build` (or import as a Synology Container Manager project).
4. Open `http://<nas>:8080/` for status + cookie refresh.

## Update yt-dlp (when Facebook breaks extraction)
`docker compose build --no-cache && docker compose up -d`

## Test
`pip install -r requirements-dev.txt && pytest -q`
```

- [ ] **Step 2: Verify the status page + cookie refresh end-to-end**
  - Open `http://<nas>:8080/` over Tailscale → enter the bearer token when prompted (stored in browser localStorage).
  - Drag a fresh `cookies.txt` onto the form → expect "✅ Cookies saved and verified."
  - Cookie badge shows 🟢 valid.

- [ ] **Step 3: Verify stale alert + fast-fail**
  - Put an invalid `cookies.txt` (or wait for real expiry), force a probe (restart container), confirm an ntfy "🔑 ... expired" arrives and the badge goes 🔴.
  - Trigger a download → expect the instant `🔑 Login expired` message (fast-fail, no long wait).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README with run/update/test instructions"
```

---

## Notes for the implementer
- **Keep yt-dlp current.** Facebook's extractor breaks periodically; rebuilding the image (`--no-cache`) pulls the latest yt-dlp. This is expected maintenance, not a bug.
- **curl_cffi is mandatory** for `--impersonate`. Task 8 Step 3 verifies it's available inside the container — do not skip it.
- **Everything stays behind Tailscale.** The bearer token is defense-in-depth, not the only line of defense; never port-forward this service.
- The **warm-session auto-cookie keeper** (spec §11) is intentionally NOT in this plan. The `cookies.txt`-on-a-volume interface leaves it a clean drop-in later.
- **URL wrapper-resolution is delegated to yt-dlp** in v1. The spec (§5.4) mentions resolving `/share/` and `fb.watch` wrappers before handing to yt-dlp; yt-dlp already follows those redirects via its own extractors, and genuinely unresolvable ones surface as `VIDEO_UNSUPPORTED` (the user re-shares the video's own "Copy link"). Explicit pre-resolution is a future enhancement, not v1 scope.
- **File Station cookie drop needs no watcher.** The spec (§5.6) lists dropping `cookies.txt` into `/config` via File Station as a fallback. Because the service reads `cookies_path` live on every download and the scheduled probe re-validates, a file dropped directly at `/config/cookies.txt` is simply picked up — no separate file-watcher is built.

---

## Appendix: Phone shortcut configuration (HTTP Shortcuts) — one-time, per phone

This is the manual setup behind spec §5.1. Do it once per phone; the maintainer can also export the finished shortcut and share it so the second phone just imports it.

1. Install **HTTP Shortcuts** (`ch.rmy.android.http_shortcuts`) from F-Droid or Play Store, and **Tailscale** (sign in, same tailnet as the NAS).
2. Create a new shortcut:
   - **Method:** `POST`
   - **URL:** `http://<nas-magicdns-name>:8080/download` (the Tailscale name, e.g. `http://nas:8080/download`)
   - **Request body:** content type `text/plain`; body = the shared text variable. In the body field tap `{ }` → insert a **Static "Share..." variable** (create it via the top-right menu → *Variables* → enable the "Allow 'Share…'" option). This makes the shared link the POST body.
   - **Request headers:** add `Authorization: Bearer <AUTH_TOKEN>` (the value from `.env`).
   - **Response handling:** set "On success/failure" to **show the response body** as a toast/dialog (so the user sees `✅ Saved` / `🔑 Login expired`). Set the request **timeout to ~300 s** so large videos finish within the wait.
   - **Trigger & Execution → enable "Direct Share target"** (Android 11+) so it appears as a one-tap tile in Facebook's share sheet.
3. Test: in Facebook, open a video → **Share → "Save to NAS"** → confirm the response toast appears.
4. (Optional) Maintainer: *Export* this shortcut (menu → Export) and send the file/QR to the second phone, which imports it and only needs to confirm the token.
