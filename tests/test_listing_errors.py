from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")


def test_sb_records_last_error():
    assert "window._sbLastError" in HTML


def test_animal_listing_explains_failure_and_checks_session():
    assert "explainWriteError('animal listing')" in HTML
    assert "await ensureSession()" in HTML
    assert "qty:Math.round(qty)" in HTML  # animal_listings.qty is an integer column
