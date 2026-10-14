from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests


def test_is_newer_when_latest_is_greater():
    import updater

    assert updater.is_newer("1.1.0", "1.0.0") is True


def test_is_newer_when_versions_are_equal():
    import updater

    assert updater.is_newer("1.0.0", "1.0.0") is False


def test_is_newer_strips_v_prefix():
    import updater

    assert updater.is_newer("v1.2.0", "1.1.9") is True


def test_check_for_update_returns_info_when_newer_asset_exists():
    import updater

    payload = {
        "tag_name": "v1.1.0",
        "assets": [
            {
                "name": "Barcode Image Downloader.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v1/app.exe",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("updater.requests.get", return_value=mock_resp):
        info = updater.check_for_update("1.0.0")

    assert info is not None
    assert info.version == "1.1.0"
    assert info.download_url == "https://github.com/o/r/releases/download/v1/app.exe"


def test_check_for_update_returns_none_when_already_current():
    import updater

    payload = {
        "tag_name": "v1.0.0",
        "assets": [
            {
                "name": "Barcode Image Downloader.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v1/app.exe",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("updater.requests.get", return_value=mock_resp):
        assert updater.check_for_update("1.0.0") is None


def test_check_for_update_returns_none_on_network_error():
    import updater

    with patch("updater.requests.get", side_effect=requests.RequestException("offline")):
        assert updater.check_for_update("1.0.0") is None


def test_check_for_update_returns_none_when_exe_asset_missing():
    import updater

    payload = {
        "tag_name": "v2.0.0",
        "assets": [{"name": "notes.txt", "browser_download_url": "https://example.com/notes"}],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("updater.requests.get", return_value=mock_resp):
        assert updater.check_for_update("1.0.0") is None


def test_check_for_update_accepts_github_dotted_exe_name():
    import updater

    payload = {
        "tag_name": "v1.1.0",
        "assets": [
            {
                "name": "Barcode.Image.Downloader.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v1/app.exe",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None

    with patch("updater.requests.get", return_value=mock_resp):
        info = updater.check_for_update("1.0.0")

    assert info is not None
    assert info.download_url == "https://github.com/o/r/releases/download/v1/app.exe"


def test_check_update_status_available_when_newer():
    import updater

    payload = {
        "tag_name": "v1.1.0",
        "assets": [
            {
                "name": "Barcode Image Downloader.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v1/app.exe",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    with patch("updater.requests.get", return_value=mock_resp):
        result = updater.check_update_status("1.0.0")

    assert result.status == "available"
    assert result.info is not None
    assert result.info.version == "1.1.0"


def test_check_update_status_current_when_same_version():
    import updater

    payload = {
        "tag_name": "v1.0.0",
        "assets": [
            {
                "name": "Barcode Image Downloader.exe",
                "browser_download_url": "https://github.com/o/r/releases/download/v1/app.exe",
            }
        ],
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload

    with patch("updater.requests.get", return_value=mock_resp):
        result = updater.check_update_status("1.0.0")

    assert result.status == "current"
    assert result.info is None


def test_check_update_status_error_on_network_failure():
    import updater

    with patch("updater.requests.get", side_effect=requests.RequestException("offline")):
        result = updater.check_update_status("1.0.0")

    assert result.status == "error"


def test_download_update_writes_bytes(tmp_path):
    import updater

    dest = tmp_path / "update.exe"
    info = updater.UpdateInfo(version="1.1.0", download_url="https://github.com/o/r/releases/download/v1/app.exe")
    mock_resp = MagicMock()
    mock_resp.iter_content.return_value = [b"MZabc", b"def"]
    mock_resp.raise_for_status.return_value = None

    with patch("updater.requests.get", return_value=mock_resp):
        path = updater.download_update(info, dest_path=str(dest))

    assert path == str(dest)
    assert dest.read_bytes() == b"MZabcdef"


