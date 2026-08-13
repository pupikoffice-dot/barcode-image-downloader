import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests


def test_search_image_urls_returns_up_to_max_results():
    import downloader
    with patch("downloader._search_upcitemdb", return_value=["u1", "u2", "u3"]), \
         patch("downloader._search_ociostock", return_value=[]), \
         patch("downloader._search_duckduckgo_images", return_value=[]), \
         patch("downloader._search_ebay_images", return_value=[]), \
         patch("downloader._search_bing_images", return_value=[]):
        result = downloader.search_image_urls("1234567890", max_results=2)
    assert result == ["u1", "u2"]


def test_search_image_urls_deduplicates_across_sources():
    import downloader
    with patch("downloader._search_upcitemdb", return_value=["u1"]), \
         patch("downloader._search_ociostock", return_value=["u1", "u2"]), \
         patch("downloader._search_duckduckgo_images", return_value=["u3"]), \
         patch("downloader._search_ebay_images", return_value=[]), \
         patch("downloader._search_bing_images", return_value=[]):
        result = downloader.search_image_urls("1234567890", max_results=4)
    assert result == ["u1", "u2", "u3"]


def test_search_image_urls_returns_empty_when_all_sources_fail():
    import downloader
    with patch("downloader._search_upcitemdb", return_value=[]), \
         patch("downloader._search_ociostock", return_value=[]), \
         patch("downloader._search_duckduckgo_images", return_value=[]), \
         patch("downloader._search_ebay_images", return_value=[]), \
         patch("downloader._search_bing_images", return_value=[]):
        result = downloader.search_image_urls("0000000000")
    assert result == []


def test_search_image_urls_falls_back_to_next_source_when_first_empty():
    import downloader
    with patch("downloader._search_upcitemdb", return_value=[]), \
         patch("downloader._search_ociostock", return_value=[]), \
         patch("downloader._search_duckduckgo_images", return_value=["ddg1", "ddg2"]), \
         patch("downloader._search_ebay_images", return_value=[]), \
         patch("downloader._search_bing_images", return_value=[]):
        result = downloader.search_image_urls("9876543210", max_results=2)
    assert result == ["ddg1", "ddg2"]


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


def test_search_upcitemdb_returns_images_from_api():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "items": [{"images": ["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"]}]
    }
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_upcitemdb("1234567890")
    assert result == ["https://img.example.com/a.jpg", "https://img.example.com/b.jpg"]


def test_search_upcitemdb_returns_empty_on_non_200():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_upcitemdb("1234567890")
    assert result == []


def test_search_upcitemdb_returns_empty_on_exception():
    import downloader
    with patch("downloader.requests.get", side_effect=Exception("network error")):
        result = downloader._search_upcitemdb("1234567890")
    assert result == []


def test_search_open_food_facts_returns_images_for_known_product():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": 1,
        "product": {
            "image_front_url": "https://images.openfoodfacts.org/front.jpg",
            "image_url": "https://images.openfoodfacts.org/img.jpg",
        }
    }
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_open_food_facts("3017620422003")
    assert "https://images.openfoodfacts.org/front.jpg" in result
    assert "https://images.openfoodfacts.org/img.jpg" in result


def test_search_open_food_facts_returns_empty_when_product_not_found():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": 0, "product": {}}
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_open_food_facts("0000000000000")
    assert result == []


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


def test_search_bing_images_extracts_murl_urls():
    import downloader
    html = '"murl":"https://example.com/img1.jpg","murl":"https://example.com/img2.jpg"'
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_bing_images("test barcode", count=4)
    assert "https://example.com/img1.jpg" in result
    assert "https://example.com/img2.jpg" in result


def test_search_bing_images_returns_empty_on_non_200():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_bing_images("test barcode")
    assert result == []


def test_search_bing_images_respects_count_limit():
    import downloader
    urls = [f'"murl":"https://example.com/img{i}.jpg"' for i in range(10)]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ",".join(urls)
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_bing_images("test", count=3)
    assert len(result) <= 3


def test_search_ebay_images_returns_empty_on_non_200():
    import downloader
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_ebay_images("1234567890")
    assert result == []


def test_search_ebay_images_extracts_ebayimg_urls():
    import downloader
    html = (
        'src="https://i.ebayimg.com/images/g/abc/s-l500.jpg" '
        'src="https://i.ebayimg.com/images/g/def/s-l500.jpg"'
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader._search_ebay_images("1234567890", count=4)
    assert "https://i.ebayimg.com/images/g/abc/s-l500.jpg" in result
    assert "https://i.ebayimg.com/images/g/def/s-l500.jpg" in result


def test_download_preview_returns_resized_thumbnail():
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (0, 128, 255)).save(buf, format="JPEG")
    mock_resp = MagicMock()
    mock_resp.content = buf.getvalue()
    mock_resp.raise_for_status.return_value = None
    with patch("downloader.requests.get", return_value=mock_resp):
        result = downloader.download_preview("https://example.com/img.jpg", max_size=220)
    assert result is not None
    assert result.size[0] <= 220
    assert result.size[1] <= 220


def test_download_preview_returns_none_on_exception():
    import downloader
    with patch("downloader.requests.get", side_effect=Exception("timeout")):
        result = downloader.download_preview("https://example.com/img.jpg")
    assert result is None


def test_format_bytes_scales_units():
    import downloader
    assert downloader.format_bytes(512) == "512 B"
    assert downloader.format_bytes(2048) == "2 KB"
    assert downloader.format_bytes(3 * 1024 * 1024) == "3.0 MB"


def test_download_preview_info_reports_original_size():
    import downloader
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (0, 128, 255)).save(buf, format="JPEG")
    raw = buf.getvalue()
    mock_resp = MagicMock()
    mock_resp.content = raw
    mock_resp.raise_for_status.return_value = None
    with patch("downloader.requests.get", return_value=mock_resp):
        info = downloader.download_preview_info("https://example.com/img.jpg", max_size=220)
    assert info.image is not None
    assert info.image.size[0] <= 220
    # Metadata describes the ORIGINAL image, not the thumbnail
    assert info.width == 800
    assert info.height == 600
    assert info.byte_size == len(raw)
    assert "800×600" in info.size_label


def test_download_preview_info_blank_on_failure():
    import downloader
    with patch("downloader.requests.get", side_effect=Exception("timeout")):
        info = downloader.download_preview_info("https://example.com/img.jpg")
    assert info.image is None
    assert info.byte_size == 0
    assert info.size_label == ""
