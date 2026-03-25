import pytest
from pathlib import Path


def test_load_credentials_returns_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=mykey\nGOOGLE_CX_ID=mycx\n")
    import downloader
    api_key, cx_id = downloader.load_credentials(env_path=str(env_file))
    assert api_key == "mykey"
    assert cx_id == "mycx"


def test_load_credentials_missing_file(tmp_path):
    import downloader
    with pytest.raises(ValueError, match="missing"):
        downloader.load_credentials(env_path=str(tmp_path / "nonexistent.env"))


def test_load_credentials_empty_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=\nGOOGLE_CX_ID=mycx\n")
    import downloader
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        downloader.load_credentials(env_path=str(env_file))


def test_load_credentials_empty_cx(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=mykey\nGOOGLE_CX_ID=\n")
    import downloader
    with pytest.raises(ValueError, match="GOOGLE_CX_ID"):
        downloader.load_credentials(env_path=str(env_file))


def test_sanitize_strips_whitespace():
    import downloader
    assert downloader.sanitize_barcode("  1234  ") == "1234"


def test_sanitize_replaces_illegal_chars():
    import downloader
    assert downloader.sanitize_barcode('bar:code/test*now?"<>|\\') == "bar_code_test_now______"


def test_sanitize_plain_barcode_unchanged():
    import downloader
    assert downloader.sanitize_barcode("1234567890123") == "1234567890123"


def test_sanitize_empty_returns_empty():
    import downloader
    assert downloader.sanitize_barcode("   ") == ""


def test_sanitize_dot_barcode_guarded():
    import downloader
    assert downloader.sanitize_barcode(".") == "_."


def test_sanitize_dotdot_barcode_guarded():
    import downloader
    assert downloader.sanitize_barcode("..") == "_.."
