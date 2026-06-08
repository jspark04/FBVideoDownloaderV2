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
