"""Check GitHub Releases for a newer Barcode Image Downloader build."""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Literal

import requests

APP_VERSION = "1.0.2"
GITHUB_REPO = "pupikoffice-dot/barcode-image-downloader"
ASSET_NAME = "Barcode Image Downloader.exe"
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

_HEADERS = {
    "User-Agent": f"BarcodeImageDownloader/{APP_VERSION}",
    "Accept": "application/vnd.github+json",
}


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    filename: str = ASSET_NAME


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
    info = UpdateInfo(
        version=latest,
        download_url=url,
        filename=asset.get("name") or ASSET_NAME,
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


def download_update(info: UpdateInfo, dest_path: str | None = None) -> str:
    """Download the release asset and return the local file path."""
    if dest_path is None:
        dest_path = os.path.join(tempfile.gettempdir(), "BarcodeImageDownloader-update.exe")
    resp = requests.get(
        info.download_url,
        headers={**_HEADERS, "Accept": "application/octet-stream"},
        timeout=120,
        stream=True,
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                fh.write(chunk)
    return dest_path


def _updater_script(target_exe: str, source_exe: str) -> str:
    """Batch file that replaces the running exe after this process exits."""
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "TARGET={target_exe}"\r\n'
        f'set "SOURCE={source_exe}"\r\n'
        ":wait\r\n"
        "ping 127.0.0.1 -n 2 >nul\r\n"
        'copy /Y "%SOURCE%" "%TARGET%" >nul\r\n'
        "if errorlevel 1 goto wait\r\n"
        'start "" "%TARGET%"\r\n'
        'del "%SOURCE%" >nul 2>&1\r\n'
        'del "%~f0"\r\n'
    )


def apply_update(new_exe_path: str) -> str:
    """Write a helper script that swaps in the new exe after this process exits.

    Refuses to run unless this is a frozen PyInstaller build, so a source
    checkout cannot overwrite python.exe.
    Returns the path of the helper script (tests inspect it; the UI launches it).
    """
    if not is_frozen():
        raise RuntimeError("Refusing to replace a non-frozen Python process")
    target = os.path.abspath(sys.executable)
    source = os.path.abspath(new_exe_path)
    script_path = os.path.join(tempfile.gettempdir(), "barcode-image-downloader-apply.bat")
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(_updater_script(target, source))
    return script_path


def launch_helper(script_path: str) -> None:
    """Start the apply-update script so it outlives this process."""
    import subprocess

    kwargs: dict = {"close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(["cmd.exe", "/c", script_path], **kwargs)
    else:
        subprocess.Popen(["/bin/sh", script_path], **kwargs)
