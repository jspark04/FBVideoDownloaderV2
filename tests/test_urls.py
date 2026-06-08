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
