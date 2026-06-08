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
