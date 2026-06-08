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
