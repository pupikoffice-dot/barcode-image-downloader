import re
from pathlib import Path

import requests
from dotenv import dotenv_values
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


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
        URL of first image result, or None if no results found.

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
