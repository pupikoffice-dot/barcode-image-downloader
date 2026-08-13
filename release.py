"""Build the Windows .exe and publish it as a GitHub Release."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from updater import APP_VERSION, ASSET_NAME, GITHUB_REPO

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "Barcode Image Downloader.spec"
EXE = ROOT / "dist" / ASSET_NAME
TAG = f"v{APP_VERSION}"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=ROOT)


def main() -> None:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)])
    if not EXE.is_file():
        raise SystemExit(f"Build failed: {EXE} not found")
    run([
        "gh", "release", "create", TAG, str(EXE),
        "--repo", GITHUB_REPO,
        "--title", TAG,
        "--notes", f"Barcode Image Downloader {TAG}",
    ])
    print(f"Published {TAG}: https://github.com/{GITHUB_REPO}/releases/tag/{TAG}")


if __name__ == "__main__":
    main()
