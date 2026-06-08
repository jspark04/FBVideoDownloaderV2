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
