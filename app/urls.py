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
