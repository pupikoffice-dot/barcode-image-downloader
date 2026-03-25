import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


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


def test_search_image_url_returns_link():
    import downloader
    mock_service = MagicMock()
    mock_service.cse().list().execute.return_value = {
        "items": [{"link": "https://example.com/image.jpg"}]
    }
    with patch("downloader.build", return_value=mock_service):
        url = downloader.search_image_url("1234567890", "fakekey", "fakecx")
    assert url == "https://example.com/image.jpg"


def test_search_image_url_returns_none_when_no_items():
    import downloader
    mock_service = MagicMock()
    mock_service.cse().list().execute.return_value = {"items": []}
    with patch("downloader.build", return_value=mock_service):
        url = downloader.search_image_url("1234567890", "fakekey", "fakecx")
    assert url is None


def test_search_image_url_returns_none_when_missing_items_key():
    import downloader
    mock_service = MagicMock()
    mock_service.cse().list().execute.return_value = {}
    with patch("downloader.build", return_value=mock_service):
        url = downloader.search_image_url("1234567890", "fakekey", "fakecx")
    assert url is None


def test_search_image_url_raises_http_error():
    import downloader
    from googleapiclient.errors import HttpError
    import httplib2
    mock_service = MagicMock()
    mock_resp = httplib2.Response({"status": "403"})
    mock_service.cse().list().execute.side_effect = HttpError(mock_resp, b"forbidden")
    with patch("downloader.build", return_value=mock_service):
        with pytest.raises(HttpError):
            downloader.search_image_url("1234567890", "fakekey", "fakecx")
