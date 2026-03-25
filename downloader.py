import io
import os
import re
import threading
import queue
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from dotenv import dotenv_values
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from PIL import Image


_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_barcode(barcode: str) -> str:
    """Strip whitespace and replace Windows-illegal filename chars with '_'.

    Returns:
        An empty string if the input is blank after stripping; callers
        should check for this before using the result as a filename.
    """
    result = _ILLEGAL_CHARS.sub("_", barcode.strip())
    if result in (".", ".."):
        result = "_" + result
    return result


def load_credentials(env_path: str | None = None) -> tuple[str, str]:
    """Load GOOGLE_API_KEY and GOOGLE_CX_ID from .env file.

    Args:
        env_path: Path to .env file. Defaults to .env next to this script.

    Returns:
        (api_key, cx_id) tuple.

    Raises:
        ValueError: if file is missing or either key is empty.
    """
    if env_path is None:
        env_path = str(Path(__file__).parent / ".env")

    if not Path(env_path).exists():
        raise ValueError(f".env file missing at {env_path}")

    config = dotenv_values(env_path)
    api_key = config.get("GOOGLE_API_KEY", "").strip()
    cx_id = config.get("GOOGLE_CX_ID", "").strip()

    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing or empty in .env")
    if not cx_id:
        raise ValueError("GOOGLE_CX_ID is missing or empty in .env")

    return api_key, cx_id


def search_image_url(barcode: str, api_key: str, cx_id: str) -> str | None:
    """Search Google Custom Search for a product image URL.

    Args:
        barcode: The barcode string (already sanitized).
        api_key: Google API key.
        cx_id: Custom Search Engine ID.

    Returns:
        URL of first image result, or None if no results or no link in first result.

    Raises:
        HttpError: if the Google API returns an error response.
    """
    service = build("customsearch", "v1", developerKey=api_key)
    result = (
        service.cse()
        .list(q=f"{barcode} product image", cx=cx_id, searchType="image", num=1)
        .execute()
    )
    items = result.get("items", [])
    if not items:
        return None
    return items[0].get("link")


def download_image_bytes(url: str, timeout: int = 10) -> bytes:
    """Download raw image bytes from a URL.

    Args:
        url: Direct URL to the image.
        timeout: Request timeout in seconds. Default 10.

    Returns:
        Raw bytes of the image.

    Raises:
        ValueError: if Content-Type header is not an image type.
        requests.HTTPError: if the HTTP response status is not 2xx.
        requests.Timeout: if the request times out.
        requests.RequestException: for other network errors.
    """
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"URL did not return an image (Content-Type: {content_type!r})")
    return response.content


def save_as_jpg(image_bytes: bytes, output_path: str) -> None:
    """Convert raw image bytes to JPEG and save to disk.

    Handles PNG, WebP, GIF (first frame), and other PIL-supported formats.
    Converts RGBA, P, LA, and other non-RGB modes to RGB before saving as JPEG.

    Args:
        image_bytes: Raw bytes of the source image (any PIL-supported format).
        output_path: Full path to write the .jpg file, including filename.

    Raises:
        PIL.UnidentifiedImageError: if bytes are not a recognizable image format.
        OSError: if the output path is not writable.
    """
    image = Image.open(io.BytesIO(image_bytes))
    # Convert to RGB (JPEG requires it; handles RGBA, P, LA, etc.)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(output_path, format="JPEG", quality=90)


@dataclass
class BarcodeResult:
    """Result of processing a single barcode."""
    barcode: str
    status: str          # "saved" | "skipped" | "no_image" | "error"
    message: str         # Human-readable detail


def process_barcode(
    barcode: str,
    output_dir: str,
    api_key: str,
    cx_id: str,
) -> BarcodeResult:
    """Search, download, and save a single barcode image.

    Args:
        barcode: Raw barcode string (will be sanitized internally).
        output_dir: Directory to save the JPG file.
        api_key: Google API key.
        cx_id: Custom Search Engine ID.

    Returns:
        BarcodeResult with status "saved", "skipped", "no_image", or "error".
    """
    safe = sanitize_barcode(barcode)
    if not safe:
        return BarcodeResult(barcode=barcode, status="error", message="Barcode is empty after sanitization")

    output_path = os.path.join(output_dir, f"{safe}.jpg")

    # Skip if already downloaded
    if os.path.exists(output_path):
        return BarcodeResult(barcode=barcode, status="skipped", message=f"{safe}.jpg already exists")

    # Search for image URL
    try:
        url = search_image_url(safe, api_key, cx_id)
    except Exception as exc:
        return BarcodeResult(barcode=barcode, status="error", message=f"Search failed: {exc}")

    if url is None:
        return BarcodeResult(barcode=barcode, status="no_image", message="No image found on Google")

    # Download and save
    try:
        image_bytes = download_image_bytes(url)
        save_as_jpg(image_bytes, output_path)
    except Exception as exc:
        return BarcodeResult(barcode=barcode, status="error", message=f"Download/save failed: {exc}")

    return BarcodeResult(barcode=barcode, status="saved", message=f"Saved as {safe}.jpg")


def run_batch(
    barcodes: list[str],
    output_dir: str,
    api_key: str,
    cx_id: str,
    progress_callback: Callable[[BarcodeResult, int, int], None] | None = None,
) -> list[BarcodeResult]:
    """Process a list of barcodes sequentially.

    Args:
        barcodes: List of raw barcode strings.
        output_dir: Directory to save JPG files.
        api_key: Google API key.
        cx_id: Custom Search Engine ID.
        progress_callback: Optional callable(result, index, total) called after each barcode.

    Returns:
        List of BarcodeResult, one per barcode.
    """
    results = []
    total = len(barcodes)
    for i, barcode in enumerate(barcodes):
        result = process_barcode(barcode, output_dir, api_key, cx_id)
        results.append(result)
        if progress_callback:
            progress_callback(result, i + 1, total)
    return results
