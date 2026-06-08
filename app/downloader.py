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
