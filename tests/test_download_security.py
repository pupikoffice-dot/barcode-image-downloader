"""URL filtering, size caps, TLS, safe filenames, atomic saves, parallel search."""
import io
import time
from unittest.mock import MagicMock, patch

import pytest
import requests
from PIL import Image, UnidentifiedImageError


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (5, 5)).save(buf, format="PNG")
    return buf.getvalue()


def test_is_safe_url_only_allows_http_and_https():
    import downloader
    assert downloader.is_safe_url("https://example.com/a.jpg")
    assert downloader.is_safe_url("http://example.com/a.jpg")
    for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "ftp://x/y", "u1", "", "https://"):
        assert not downloader.is_safe_url(bad), bad


def test_search_image_urls_drops_unsafe_urls():
    import downloader
    with patch("downloader._search_upcitemdb", return_value=["file:///C:/x.jpg", "https://ok.example/a.jpg"]), \
         patch("downloader._search_ociostock", return_value=[]), \
         patch("downloader._search_duckduckgo_images", return_value=["javascript:alert(1)"]), \
         patch("downloader._search_ebay_images", return_value=[]), \
         patch("downloader._search_bing_images", return_value=[]):
        assert downloader.search_image_urls("123", max_results=4) == ["https://ok.example/a.jpg"]


def test_search_image_urls_queries_sources_in_parallel():
    import downloader

    def slow(_barcode, count=8):
        time.sleep(0.3)
        return []

    def slow_upc(_barcode):
        time.sleep(0.3)
        return []

    with patch("downloader._search_upcitemdb", side_effect=slow_upc), \
         patch("downloader._search_ociostock", side_effect=slow), \
         patch("downloader._search_duckduckgo_images", side_effect=slow), \
         patch("downloader._search_ebay_images", side_effect=slow), \
         patch("downloader._search_bing_images", side_effect=slow):
        start = time.monotonic()
        downloader.search_image_urls("123")
        elapsed = time.monotonic() - start
    assert elapsed < 1.0  # sequential would be ~1.5s


def test_sanitize_filename_part_blocks_path_traversal():
    import downloader
    assert "\\" not in downloader.sanitize_filename_part("..\\..\\Windows")
    assert "/" not in downloader.sanitize_filename_part("../../etc")
    assert downloader.sanitize_filename_part("  FNK  ") == "FNK"
    assert downloader.sanitize_filename_part("...") == ""


def test_download_image_bytes_rejects_oversized_response():
    import downloader
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "image/jpeg", "Content-Length": str(downloader.MAX_IMAGE_BYTES + 1)}
    mock_response.raise_for_status.return_value = None
    with patch("downloader._http.get", return_value=mock_response):
        with pytest.raises(ValueError, match="too large"):
            downloader.download_image_bytes("https://example.com/huge.jpg")


def test_download_image_bytes_stops_reading_past_cap():
    import downloader
    mock_response = MagicMock()
    mock_response.headers = {"Content-Type": "image/jpeg"}
    mock_response.raise_for_status.return_value = None
    chunk = b"x" * (1024 * 1024)
    mock_response.iter_content.return_value = iter([chunk] * (downloader.MAX_IMAGE_BYTES // len(chunk) + 2))
    with patch("downloader._http.get", return_value=mock_response):
        with pytest.raises(ValueError, match="larger than"):
            downloader.download_image_bytes("https://example.com/endless.jpg")


def test_download_image_bytes_refuses_file_url():
    import downloader
    with patch("downloader._http.get") as mock_get:
        with pytest.raises(ValueError, match="non-http"):
            downloader.download_image_bytes("file:///C:/secret.jpg")
    mock_get.assert_not_called()


def test_download_preview_info_keeps_raw_bytes_for_saving():
    import downloader
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "green").save(buf, format="JPEG")
    raw = buf.getvalue()
    mock_resp = MagicMock()
    mock_resp.iter_content.return_value = [raw]
    mock_resp.raise_for_status.return_value = None
    with patch("downloader._http.get", return_value=mock_resp):
        info = downloader.download_preview_info("https://example.com/img.jpg")
    assert info.raw == raw


def test_download_preview_info_does_not_disable_tls_verification():
    import downloader
    with patch("downloader._http.get", side_effect=requests.exceptions.SSLError("bad cert")) as mock_get:
        info = downloader.download_preview_info("https://example.com/img.jpg")
    assert info.image is None
    for call in mock_get.call_args_list:
        assert call.kwargs.get("verify", True) is True


def test_download_previews_preserves_order():
    import downloader

    def fake(url, max_size=220):
        time.sleep(0.05 if url.endswith("1") else 0)
        return downloader.PreviewInfo(image=None, byte_size=int(url[-1]))

    with patch("downloader.download_preview_info", side_effect=fake):
        infos = downloader.download_previews(["https://e/1", "https://e/2", "https://e/3"])
    assert [i.byte_size for i in infos] == [1, 2, 3]


def test_save_as_jpg_no_overwrite_raises_when_exists(tmp_path):
    import downloader
    out = tmp_path / "taken.jpg"
    out.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        downloader.save_as_jpg(_png_bytes(), str(out), overwrite=False)
    assert out.read_bytes() == b"existing"
    assert not list(tmp_path.glob("*.part"))


def test_save_as_jpg_no_overwrite_creates_new_file(tmp_path):
    import downloader
    out = tmp_path / "fresh.jpg"
    downloader.save_as_jpg(_png_bytes(), str(out), overwrite=False)
    assert Image.open(out).format == "JPEG"
    assert [p.name for p in tmp_path.iterdir()] == ["fresh.jpg"]


def test_save_as_jpg_leaves_no_partial_file_on_bad_input(tmp_path):
    import downloader
    with pytest.raises(UnidentifiedImageError):
        downloader.save_as_jpg(b"junk", str(tmp_path / "bad.jpg"))
    assert list(tmp_path.iterdir()) == []
