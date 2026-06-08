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
