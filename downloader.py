import io
import re
from pathlib import Path

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
    Converts RGBA/P mode images to RGB before saving as JPEG.

    Args:
        image_bytes: Raw bytes of the source image (any PIL-supported format).
        output_path: Full path to write the .jpg file, including filename.

    Raises:
        PIL.UnidentifiedImageError: if bytes are not a recognizable image format.
        OSError: if the output path is not writable.
    """
    image = Image.open(io.BytesIO(image_bytes))
    # Convert palette or RGBA to RGB so JPEG encoder accepts it
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(output_path, format="JPEG", quality=90)
