import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests


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


def test_download_image_bytes_returns_content():
    import downloader
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Content-Type": "image/jpeg"}
    mock_response.content = b"fakeimagebytes"
    mock_response.raise_for_status.return_value = None
    with patch("downloader.requests.get", return_value=mock_response):
        result = downloader.download_image_bytes("https://example.com/img.jpg")
    assert result == b"fakeimagebytes"


def test_download_image_bytes_raises_on_non_image_content_type():
    import downloader
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"Content-Type": "text/html"}
    mock_response.content = b"<html>"
    mock_response.raise_for_status.return_value = None
    with patch("downloader.requests.get", return_value=mock_response):
        with pytest.raises(ValueError, match="Content-Type"):
            downloader.download_image_bytes("https://example.com/page.html")


def test_download_image_bytes_raises_on_http_error():
    import downloader
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("404")
    with patch("downloader.requests.get", return_value=mock_response):
        with pytest.raises(requests.HTTPError):
            downloader.download_image_bytes("https://example.com/missing.jpg")


def test_download_image_bytes_raises_on_timeout():
    import downloader
    with patch("downloader.requests.get", side_effect=requests.Timeout):
        with pytest.raises(requests.Timeout):
            downloader.download_image_bytes("https://example.com/slow.jpg")


def test_download_image_bytes_accepts_various_image_types():
    import downloader
    for ct in ("image/png", "image/webp", "image/gif"):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": ct}
        mock_response.content = b"bytes"
        mock_response.raise_for_status.return_value = None
        with patch("downloader.requests.get", return_value=mock_response):
            result = downloader.download_image_bytes("https://example.com/img")
        assert result == b"bytes"


def test_download_image_bytes_raises_on_request_exception():
    import downloader
    with patch("downloader.requests.get", side_effect=requests.RequestException("connection refused")):
        with pytest.raises(requests.RequestException):
            downloader.download_image_bytes("https://example.com/img.jpg")


def test_save_as_jpg_creates_file(tmp_path):
    import downloader
    from PIL import Image
    import io
    # Create a real PNG in memory
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(buf, format="PNG")
    out = str(tmp_path / "test.jpg")
    downloader.save_as_jpg(buf.getvalue(), out)
    assert (tmp_path / "test.jpg").exists()


def test_save_as_jpg_output_is_valid_jpeg(tmp_path):
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    out = str(tmp_path / "out.jpg")
    downloader.save_as_jpg(buf.getvalue(), out)
    loaded = Image.open(out)
    assert loaded.format == "JPEG"


def test_save_as_jpg_converts_rgba(tmp_path):
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 255, 0, 128)).save(buf, format="PNG")
    out = str(tmp_path / "rgba.jpg")
    downloader.save_as_jpg(buf.getvalue(), out)
    loaded = Image.open(out)
    assert loaded.mode == "RGB"


def test_save_as_jpg_converts_png_palette(tmp_path):
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    img = Image.new("RGB", (10, 10)).convert("P")
    img.save(buf, format="PNG")
    out = str(tmp_path / "palette.jpg")
    downloader.save_as_jpg(buf.getvalue(), out)
    loaded = Image.open(out)
    assert loaded.format == "JPEG"
    assert loaded.mode == "RGB"


def test_save_as_jpg_raises_on_invalid_bytes(tmp_path):
    import downloader
    from PIL import UnidentifiedImageError
    with pytest.raises(UnidentifiedImageError):
        downloader.save_as_jpg(b"notanimage", str(tmp_path / "bad.jpg"))


def test_save_as_jpg_converts_la_mode(tmp_path):
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("LA", (10, 10)).save(buf, format="PNG")
    out = str(tmp_path / "la.jpg")
    downloader.save_as_jpg(buf.getvalue(), out)
    loaded = Image.open(out)
    assert loaded.mode == "RGB"


def test_process_barcode_returns_saved_on_success(tmp_path):
    import downloader
    with patch("downloader.search_image_url", return_value="https://img.example.com/a.jpg"), \
         patch("downloader.download_image_bytes", return_value=b"bytes"), \
         patch("downloader.save_as_jpg") as mock_save:
        result = downloader.process_barcode("1234567890", str(tmp_path), "key", "cx")
    assert result.status == "saved"
    assert "1234567890.jpg" in result.message


def test_process_barcode_skips_existing_file(tmp_path):
    import downloader
    (tmp_path / "1234567890.jpg").write_bytes(b"existing")
    result = downloader.process_barcode("1234567890", str(tmp_path), "key", "cx")
    assert result.status == "skipped"


def test_process_barcode_returns_no_image_when_url_none(tmp_path):
    import downloader
    with patch("downloader.search_image_url", return_value=None):
        result = downloader.process_barcode("1234567890", str(tmp_path), "key", "cx")
    assert result.status == "no_image"


def test_process_barcode_returns_error_on_search_exception(tmp_path):
    import downloader
    with patch("downloader.search_image_url", side_effect=Exception("API down")):
        result = downloader.process_barcode("1234567890", str(tmp_path), "key", "cx")
    assert result.status == "error"
    assert "API down" in result.message


def test_process_barcode_returns_error_on_empty_after_sanitize(tmp_path):
    import downloader
    result = downloader.process_barcode("   ", str(tmp_path), "key", "cx")
    assert result.status == "error"


def test_run_batch_calls_progress_callback(tmp_path):
    import downloader
    with patch("downloader.process_barcode", return_value=downloader.BarcodeResult("123", "saved", "ok")):
        calls = []
        downloader.run_batch(["123", "456"], str(tmp_path), "key", "cx",
                             progress_callback=lambda r, i, t: calls.append((i, t)))
    assert calls == [(1, 2), (2, 2)]


def test_run_batch_returns_all_results(tmp_path):
    import downloader
    with patch("downloader.process_barcode", return_value=downloader.BarcodeResult("123", "saved", "ok")):
        results = downloader.run_batch(["a", "b", "c"], str(tmp_path), "key", "cx")
    assert len(results) == 3
