"""Updater integrity checks and the script-free install flow."""
import hashlib
from unittest.mock import MagicMock, patch

import pytest

GH_URL = "https://github.com/o/r/releases/download/v1.1.0/app.exe"


def _release_payload(url=GH_URL, **asset_extra):
    return {
        "tag_name": "v1.1.0",
        "assets": [{"name": "Barcode Image Downloader.exe", "browser_download_url": url, **asset_extra}],
    }


def _ok_json(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    return resp


def _stream(chunks):
    resp = MagicMock()
    resp.iter_content.return_value = chunks
    resp.raise_for_status.return_value = None
    return resp


def test_check_update_status_rejects_asset_outside_github():
    import updater

    with patch("updater.requests.get", return_value=_ok_json(_release_payload(url="https://evil.example/app.exe"))):
        assert updater.check_update_status("1.0.0").status == "error"


def test_check_update_status_rejects_plain_http():
    import updater

    with patch("updater.requests.get", return_value=_ok_json(_release_payload(url="http://github.com/o/r/app.exe"))):
        assert updater.check_update_status("1.0.0").status == "error"


def test_check_update_status_reads_digest_and_size():
    import updater

    digest = "sha256:" + "ab" * 32
    with patch("updater.requests.get", return_value=_ok_json(_release_payload(size=123, digest=digest))):
        result = updater.check_update_status("1.0.0")
    assert result.info.sha256 == "ab" * 32
    assert result.info.size == 123


def test_download_update_verifies_sha256(tmp_path):
    import updater

    body = b"MZ" + b"x" * 100
    info = updater.UpdateInfo(
        version="1.1.0", download_url=GH_URL,
        size=len(body), sha256=hashlib.sha256(body).hexdigest(),
    )
    dest = tmp_path / "new.exe"
    with patch("updater.requests.get", return_value=_stream([body])):
        assert updater.download_update(info, dest_path=str(dest)) == str(dest)
    assert dest.read_bytes() == body


def test_download_update_deletes_file_on_hash_mismatch(tmp_path):
    import updater

    info = updater.UpdateInfo(version="1.1.0", download_url=GH_URL, sha256="0" * 64)
    dest = tmp_path / "new.exe"
    with patch("updater.requests.get", return_value=_stream([b"MZtampered"])):
        with pytest.raises(ValueError, match="SHA-256"):
            updater.download_update(info, dest_path=str(dest))
    assert not dest.exists()


def test_download_update_rejects_size_mismatch(tmp_path):
    import updater

    info = updater.UpdateInfo(version="1.1.0", download_url=GH_URL, size=999)
    dest = tmp_path / "new.exe"
    with patch("updater.requests.get", return_value=_stream([b"MZshort"])):
        with pytest.raises(ValueError, match="size"):
            updater.download_update(info, dest_path=str(dest))
    assert not dest.exists()


def test_download_update_rejects_non_executable(tmp_path):
    import updater

    info = updater.UpdateInfo(version="1.1.0", download_url=GH_URL)
    dest = tmp_path / "new.exe"
    with patch("updater.requests.get", return_value=_stream([b"<html>error page</html>"])):
        with pytest.raises(ValueError, match="Windows program"):
            updater.download_update(info, dest_path=str(dest))
    assert not dest.exists()


def test_download_update_refuses_untrusted_url(tmp_path):
    import updater

    info = updater.UpdateInfo(version="1.1.0", download_url="https://evil.example/app.exe")
    with patch("updater.requests.get") as mock_get:
        with pytest.raises(ValueError, match="github.com"):
            updater.download_update(info, dest_path=str(tmp_path / "x.exe"))
    mock_get.assert_not_called()


def test_install_update_refuses_when_not_frozen():
    import updater

    with patch("updater.is_frozen", return_value=False):
        with pytest.raises(RuntimeError, match="non-frozen"):
            updater.install_update("new.exe")


def test_install_update_swaps_exe_and_keeps_backup(tmp_path):
    import updater

    current = tmp_path / "app.exe"
    current.write_bytes(b"old")
    new = tmp_path / "app.exe.download"
    new.write_bytes(b"new")

    with patch("updater.is_frozen", return_value=True), \
         patch("updater.sys.executable", str(current)):
        target = updater.install_update(str(new))

    assert target == str(current)
    assert current.read_bytes() == b"new"
    assert (tmp_path / "app.exe.old").read_bytes() == b"old"
    assert not new.exists()


def test_install_update_rolls_back_when_new_file_missing(tmp_path):
    import updater

    current = tmp_path / "app.exe"
    current.write_bytes(b"old")

    with patch("updater.is_frozen", return_value=True), \
         patch("updater.sys.executable", str(current)):
        with pytest.raises(OSError):
            updater.install_update(str(tmp_path / "missing.exe"))

    assert current.read_bytes() == b"old"


def test_no_batch_script_updater_remains():
    import updater

    assert not hasattr(updater, "apply_update")
    assert not hasattr(updater, "_updater_script")
    assert not hasattr(updater, "launch_helper")
