"""Check GitHub Releases for a newer Barcode Image Downloader build.

Update flow (no batch scripts, no cmd.exe — those patterns trip antivirus
heuristics):
  1. download the new exe next to the running one and verify it
     (HTTPS from github.com, size, SHA-256 digest from the GitHub API, PE header)
  2. rename the running exe to "<name>.old"  (Windows allows renaming a running exe)
  3. move the new exe into the original path and start it
  4. the new process deletes "<name>.old" on startup
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import requests

APP_VERSION = "1.1.0"
GITHUB_REPO = "pupikoffice-dot/barcode-image-downloader"
ASSET_NAME = "Barcode Image Downloader.exe"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Release assets must be served from GitHub over HTTPS.
_ALLOWED_DOWNLOAD_HOSTS = ("github.com",)
_MAX_UPDATE_BYTES = 300 * 1024 * 1024

_HEADERS = {
    "User-Agent": f"BarcodeImageDownloader/{APP_VERSION}",
    "Accept": "application/vnd.github+json",
}


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    filename: str = ASSET_NAME
    size: int = 0          # expected byte size from the API (0 = unknown)
    sha256: str = ""       # expected hex digest from the API ("" = unknown)


def normalize_version(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lstrip("vV")
    parts = [p for p in cleaned.split(".") if p]
    return tuple(int(p) for p in parts)


def is_newer(latest: str, current: str) -> bool:
    try:
        return normalize_version(latest) > normalize_version(current)
    except ValueError:
        return False


def _strip_v(tag: str) -> str:
    return tag.strip().lstrip("vV")


@dataclass(frozen=True)
class UpdateCheckResult:
    status: Literal["available", "current", "error"]
    info: UpdateInfo | None = None
    message: str = ""


def _pick_exe_asset(assets: list) -> dict | None:
    """GitHub replaces spaces in upload names with dots, so match both forms."""
    dotted = ASSET_NAME.replace(" ", ".")
    fallback = None
    for asset in assets or []:
        name = asset.get("name") or ""
        if name in (ASSET_NAME, dotted):
            return asset
        if fallback is None and name.lower().endswith(".exe"):
            fallback = asset
    return fallback


def is_trusted_download_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and host in _ALLOWED_DOWNLOAD_HOSTS


def _parse_sha256(digest: str | None) -> str:
    """GitHub reports asset digests as 'sha256:<hex>'."""
    if not digest or not isinstance(digest, str):
        return ""
    algo, _, value = digest.partition(":")
    value = value.strip().lower()
    if algo.lower() == "sha256" and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return ""


def check_update_status(current_version: str = APP_VERSION) -> UpdateCheckResult:
    """Check GitHub Releases: available, already current, or error."""
    try:
        resp = requests.get(API_URL, headers=_HEADERS, timeout=12)
        if resp.status_code != 200:
            return UpdateCheckResult("error", message=f"HTTP {resp.status_code}")
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return UpdateCheckResult("error", message=str(exc))

    tag = data.get("tag_name") or ""
    latest = _strip_v(str(tag))
    if not latest:
        return UpdateCheckResult("error", message="No version tag on latest release")
    if not is_newer(latest, current_version):
        return UpdateCheckResult("current")

    asset = _pick_exe_asset(data.get("assets") or [])
    if not asset:
        return UpdateCheckResult("error", message="No .exe on the latest release")
    url = asset.get("browser_download_url") or ""
    if not url:
        return UpdateCheckResult("error", message="Release asset has no download URL")
    if not is_trusted_download_url(url):
        return UpdateCheckResult("error", message="Release asset is not hosted on github.com")
    size = asset.get("size")
    info = UpdateInfo(
        version=latest,
        download_url=url,
        filename=asset.get("name") or ASSET_NAME,
        size=size if isinstance(size, int) and size > 0 else 0,
        sha256=_parse_sha256(asset.get("digest")),
    )
    return UpdateCheckResult("available", info=info)


def check_for_update(current_version: str = APP_VERSION) -> UpdateInfo | None:
    """Return update info if GitHub has a newer .exe; otherwise None.

    Network and API errors are treated as "no update" so a launch is never blocked.
    """
    result = check_update_status(current_version)
    if result.status == "available":
        return result.info
    return None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _default_download_path() -> str:
    """Next to the running exe, so the final move is a same-folder rename."""
    if is_frozen():
        return os.path.abspath(sys.executable) + ".download"
    return os.path.join(tempfile.gettempdir(), "BarcodeImageDownloader-update.exe")


def download_update(info: UpdateInfo, dest_path: str | None = None) -> str:
    """Download and verify the release asset; return the local file path.

    Raises ValueError if the file fails any integrity check (and deletes it).
    """
    if not is_trusted_download_url(info.download_url):
        raise ValueError("Refusing to download an update from outside github.com")
    if dest_path is None:
        dest_path = _default_download_path()

    hasher = hashlib.sha256()
    total = 0
    head = b""
    resp = requests.get(
        info.download_url,
        headers={**_HEADERS, "Accept": "application/octet-stream"},
        timeout=120,
        stream=True,
    )
    try:
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                if len(head) < 2:
                    head += chunk[:2]
                total += len(chunk)
                if total > _MAX_UPDATE_BYTES:
                    raise ValueError("Update file is unexpectedly large")
                hasher.update(chunk)
                fh.write(chunk)
    except BaseException:
        resp.close()
        _silent_remove(dest_path)
        raise
    resp.close()

    problem = ""
    if total == 0:
        problem = "Downloaded update is empty"
    elif info.size and total != info.size:
        problem = f"Downloaded size {total} does not match expected {info.size}"
    elif info.sha256 and hasher.hexdigest() != info.sha256:
        problem = "Downloaded update failed the SHA-256 check"
    elif info.filename.lower().endswith(".exe") and not head.startswith(b"MZ"):
        problem = "Downloaded update is not a Windows program"
    if problem:
        _silent_remove(dest_path)
        raise ValueError(problem)
    return dest_path


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def install_update(new_exe_path: str) -> str:
    """Swap the downloaded exe into place of the running one; return its path.

    Refuses to run unless this is a frozen PyInstaller build, so a source
    checkout cannot overwrite python.exe. On failure the original exe is restored.
    """
    if not is_frozen():
        raise RuntimeError("Refusing to replace a non-frozen Python process")
    target = os.path.abspath(sys.executable)
    source = os.path.abspath(new_exe_path)
    backup = target + ".old"

    _silent_remove(backup)  # left over from an earlier update
    os.replace(target, backup)          # renaming a running exe is allowed
    try:
        os.replace(source, target)
    except BaseException:
        os.replace(backup, target)      # roll back
        raise
    return target


def relaunch(exe_path: str) -> None:
    """Start the (new) exe as an independent process."""
    env = dict(os.environ)
    # Tell the PyInstaller bootloader this is a fresh app, not a child of ours.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    kwargs: dict = {"close_fds": True, "env": env, "cwd": os.path.dirname(exe_path)}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen([exe_path], **kwargs)


def _stale_update_files() -> list[str]:
    paths = [
        # Files from the 1.0.x batch-script updater.
        os.path.join(tempfile.gettempdir(), "BarcodeImageDownloader-update.exe"),
        os.path.join(tempfile.gettempdir(), "barcode-image-downloader-apply.bat"),
    ]
    if is_frozen():
        # ".download" is not listed: a new update may be downloading to it right now.
        paths.append(os.path.abspath(sys.executable) + ".old")
    return paths


def cleanup_previous_update(retries: int = 10, delay: float = 1.0) -> None:
    """Delete leftovers from a previous update.

    The old process may still be exiting, so locked files are retried for a
    few seconds in a background thread.
    """
    def _work() -> None:
        pending = [p for p in _stale_update_files() if os.path.exists(p)]
        for _ in range(retries):
            still = []
            for path in pending:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    still.append(path)
            if not still:
                return
            pending = still
            time.sleep(delay)

    threading.Thread(target=_work, daemon=True).start()
