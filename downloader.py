import re
from pathlib import Path

from dotenv import dotenv_values


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
