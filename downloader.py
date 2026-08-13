import io
import os
import re
import warnings
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote_plus

import requests
import urllib3
from PIL import Image

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def sanitize_barcode(barcode: str) -> str:
    """Strip whitespace and replace Windows-illegal filename chars with '_'."""
    result = _ILLEGAL_CHARS.sub("_", barcode.strip())
    if result in (".", ".."):
        result = "_" + result
    return result


# ------------------------------------------------------------------ #
#  Source: UPC Item DB (API)                                           #
# ------------------------------------------------------------------ #

def _search_upcitemdb(barcode: str) -> list[str]:
    """UPC Item DB — returns product image URLs from structured API."""
    url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return []
        return items[0].get("images", [])
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Source: Open Food Facts (API)                                       #
# ------------------------------------------------------------------ #

def _search_open_food_facts(barcode: str) -> list[str]:
    """Open Food Facts — returns product image URLs from structured API."""
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("status") != 1:
            return []
        product = data.get("product", {})
        urls: list[str] = []
        for key in (
            "image_front_url", "image_url", "image_small_url",
            "image_nutrition_url", "image_ingredients_url",
        ):
            img = product.get(key)
            if img and img not in urls:
                urls.append(img)
        return urls
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Source: DuckDuckGo Images (scrape)                                  #
# ------------------------------------------------------------------ #

def _search_duckduckgo_images(barcode: str, count: int = 8) -> list[str]:
    """DuckDuckGo Images — two-step vqd approach; excellent product coverage."""
    query = quote_plus(barcode)
    search_url = f"https://duckduckgo.com/?q={query}&iax=images&ia=images"
    ddg_headers = {**_HEADERS, "Referer": "https://duckduckgo.com/"}
    try:
        resp = requests.get(search_url, headers=ddg_headers, timeout=12)
        if resp.status_code != 200:
            return []

        # Extract vqd token (multiple patterns for robustness)
        vqd = None
        for pat in [
            re.compile(r'vqd=([^&"\'<>\s]+)'),
            re.compile(r'"vqd"\s*:\s*"([^"]+)"'),
            re.compile(r"vqd='([^']+)'"),
        ]:
            m = pat.search(resp.text)
            if m:
                vqd = m.group(1)
                break

        if not vqd:
            return []

        # Fetch JSON image results
        api_url = (
            f"https://duckduckgo.com/i.js"
            f"?q={query}&vqd={vqd}&f=,,,,,&p=1&v7exp=a"
        )
        resp2 = requests.get(
            api_url,
            headers={**ddg_headers, "Accept": "application/json", "Referer": search_url},
            timeout=12,
        )
        if resp2.status_code != 200:
            return []

        data = resp2.json()
        results: list[str] = []
        for item in data.get("results", []):
            img_url = item.get("image", "")
            if img_url and img_url not in results:
                results.append(img_url)
            if len(results) >= count:
                break
        return results
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Source: eBay product search (scrape)                                #
# ------------------------------------------------------------------ #

def _search_ebay_images(barcode: str, count: int = 8) -> list[str]:
    """eBay product search — returns product listing images; great for toys/collectibles."""
    query = quote_plus(barcode)
    url = f"https://www.ebay.com/sch/i.html?_nkw={query}&_sacat=0"
    ebay_headers = {**_HEADERS, "Referer": "https://www.ebay.com/"}
    try:
        resp = requests.get(url, headers=ebay_headers, timeout=12)
        if resp.status_code != 200:
            return []

        # Extract eBay product image URLs, prefer s-l500 (medium quality)
        all_imgs = re.findall(
            r'https://i\.ebayimg\.com/[^\s"\'\\<>]+', resp.text
        )
        # Deduplicate and prefer larger images
        seen: set[str] = set()
        results: list[str] = []
        for img in all_imgs:
            # Normalise to s-l500 for consistency
            norm = re.sub(r's-l\d+', 's-l500', img)
            if norm not in seen:
                seen.add(norm)
                results.append(norm)
            if len(results) >= count:
                break
        return results
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Source: Ociostock (scrape)                                          #
# ------------------------------------------------------------------ #

def _search_ociostock(barcode: str, count: int = 8) -> list[str]:
    """Ociostock B2B catalog — text search by EAN/barcode, extract product images."""
    from urllib.parse import quote_plus
    search_url = (
        f"https://www.ociostock.com/buscador/"
        f"?filtro_texto={quote_plus(barcode)}&idsite=1&idioma=50&tipo_busqueda=1"
    )
    ocio_headers = {**_HEADERS, "Referer": "https://www.ociostock.com/"}
    try:
        resp = requests.get(search_url, headers=ocio_headers, timeout=15)
        if resp.status_code != 200:
            return []

        # Find first product page link (pattern: /slug-p-1-50-{ID}/)
        product_links = re.findall(
            r'href="(https?://(?:www\.)?ociostock\.com/[^"]*-p-1-50-\d+/)"',
            resp.text,
        )
        if not product_links:
            # Also try relative URLs
            rel_links = re.findall(r'href="(/[^"]*-p-1-50-\d+/)"', resp.text)
            if not rel_links:
                return []
            product_links = [f"https://www.ociostock.com{lnk}" for lnk in rel_links]

        # Fetch first product page
        prod_resp = requests.get(
            product_links[0], headers=ocio_headers, timeout=15
        )
        if prod_resp.status_code != 200:
            return []

        # Extract all product image URLs; prefer larger size variants
        # Pattern: /productos/imagenes/img_{ID}_{HASH}_{SIZE}.jpg
        all_imgs = re.findall(
            r'(https?://(?:www\.)?ociostock\.com/productos/imagenes/img_[^"\'<>\s]+\.jpg)',
            prod_resp.text,
        )
        if not all_imgs:
            all_imgs = re.findall(
                r'(/productos/imagenes/img_[^"\'<>\s]+\.jpg)',
                prod_resp.text,
            )
            all_imgs = [f"https://www.ociostock.com{u}" for u in all_imgs]

        # Deduplicate; prefer higher size suffix (bigger number = larger image)
        seen_base: dict[str, str] = {}  # base_key -> best url
        for img_url in all_imgs:
            m = re.match(
                r'(.+/img_(\d+)_([a-f0-9]+))_(\d+)\.jpg', img_url
            )
            if m:
                base_key = m.group(1)  # URL without size suffix
                size_num = int(m.group(4))
                existing = seen_base.get(base_key)
                if existing is None:
                    seen_base[base_key] = (size_num, img_url)
                elif size_num > seen_base[base_key][0]:
                    seen_base[base_key] = (size_num, img_url)
            else:
                # no size pattern, just add as-is
                if img_url not in seen_base:
                    seen_base[img_url] = (0, img_url)

        results = [v[1] for v in seen_base.values()]
        return results[:count]
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Source: Bing Images (scrape — fallback)                             #
# ------------------------------------------------------------------ #

def _search_bing_images(barcode: str, count: int = 8) -> list[str]:
    """Bing Images — fallback scrape using murl JSON extraction."""
    query = quote_plus(barcode)
    url = (
        f"https://www.bing.com/images/search"
        f"?q={query}&form=HDRSC2&first=1&tsc=ImageHoverTitle"
    )
    bing_headers = {**_HEADERS, "Referer": "https://www.bing.com/"}
    try:
        resp = requests.get(url, headers=bing_headers, timeout=12)
        if resp.status_code != 200:
            return []

        results: list[str] = []
        # Bing embeds JSON: "murl":"URL"  or  murl&quot;:&quot;URL&quot;
        for pattern in [
            re.compile(r'"murl"\s*:\s*"(https?://[^"]+)"'),
            re.compile(r'murl&quot;:&quot;(https?://[^&]+?)&quot;'),
        ]:
            for match in pattern.findall(resp.text):
                clean = match.strip()
                if clean.startswith("http") and len(clean) < 600 and clean not in results:
                    results.append(clean)
                if len(results) >= count:
                    return results
        return results
    except Exception:
        return []


# ------------------------------------------------------------------ #
#  Combined search                                                     #
# ------------------------------------------------------------------ #

def search_image_urls(barcode: str, max_results: int = 4) -> list[str]:
    """Search multiple sources for product image URLs.

    Returns up to `max_results` unique image URLs.
    Source order (best product coverage first):
      1. UPC Item DB        — structured API, authoritative
      2. Ociostock          — B2B catalog; great for toys/collectibles (EAN search)
      3. DuckDuckGo Images  — best free scrape; excellent product coverage
      4. eBay product search — great for toys, collectibles, consumer goods
      5. Bing Images        — final fallback
    """
    found: list[str] = []

    # Structured API source first
    for url in _search_upcitemdb(barcode):
        if url not in found:
            found.append(url)
        if len(found) >= max_results:
            return found[:max_results]

    # Image-search scrapers (each asked for a few extra to handle dupes/failures)
    for fn in (_search_ociostock, _search_duckduckgo_images, _search_ebay_images, _search_bing_images):
        if len(found) >= max_results:
            break
        needed = max_results - len(found) + 4
        for url in fn(barcode, count=needed):
            if url not in found:
                found.append(url)
            if len(found) >= max_results:
                break

    return found[:max_results]


# ------------------------------------------------------------------ #
#  Image download helpers                                              #
# ------------------------------------------------------------------ #

def download_image_bytes(url: str, timeout: int = 15) -> bytes:
    """Download raw image bytes from a URL."""
    response = requests.get(url, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if content_type and not content_type.startswith("image/"):
        raise ValueError(f"URL did not return an image (Content-Type: {content_type!r})")
    return response.content


@dataclass
class PreviewInfo:
    """A preview thumbnail plus the source image's real size on the wire."""
    image: Image.Image | None
    byte_size: int = 0          # bytes of the original file as downloaded
    width: int = 0              # original pixel width  (before thumbnailing)
    height: int = 0             # original pixel height (before thumbnailing)

    @property
    def size_label(self) -> str:
        """Short human-readable '1200x1200 - 245 KB' style summary."""
        if self.image is None:
            return ""
        parts = []
        if self.width and self.height:
            parts.append(f"{self.width}×{self.height}")
        if self.byte_size:
            parts.append(format_bytes(self.byte_size))
        return "  ·  ".join(parts)


def format_bytes(num_bytes: int) -> str:
    """Format a byte count as KB/MB with sensible precision."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"
    return f"{kb / 1024:.1f} MB"


def download_preview_info(url: str, max_size: int = 220) -> PreviewInfo:
    """Download an image once, returning a thumbnail plus original size metadata.

    Same lenient fetch as `download_preview`. The byte count is the original
    file as served — not the size of the JPEG this app later writes to disk,
    which is re-encoded at quality 90.
    """
    try:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            raw = resp.content
        except requests.exceptions.SSLError:
            resp = requests.get(url, headers=_HEADERS, timeout=15, verify=False)
            resp.raise_for_status()
            raw = resp.content
        img = Image.open(io.BytesIO(raw))
        orig_w, orig_h = img.size
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        return PreviewInfo(image=img, byte_size=len(raw), width=orig_w, height=orig_h)
    except Exception:
        return PreviewInfo(image=None)


def download_preview(url: str, max_size: int = 220) -> Image.Image | None:
    """Download an image and return a resized thumbnail for preview.

    Uses a lenient fetch (no content-type gating, SSL errors tolerated) so that
    CDN URLs that return application/octet-stream or have cert issues still render.
    """
    return download_preview_info(url, max_size=max_size).image


def save_as_jpg(image_bytes: bytes, output_path: str) -> None:
    """Convert raw image bytes to JPEG and save to disk."""
    image = Image.open(io.BytesIO(image_bytes))
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(output_path, format="JPEG", quality=90)


def download_first_image(barcode: str, output_path: str) -> BarcodeResult:
    """Search for the barcode and save the first image that actually downloads.

    Skips if `output_path` already exists. If the first URL fails, later search
    results are tried so a single bad CDN does not fail the barcode.
    """
    if os.path.exists(output_path):
        return BarcodeResult(barcode, "skipped", "file already exists")

    urls = search_image_urls(barcode, max_results=4)
    if not urls:
        return BarcodeResult(barcode, "no_image", "no images found")

    last_error = "no images found"
    filename = os.path.basename(output_path)
    for url in urls:
        try:
            raw = download_image_bytes(url)
            save_as_jpg(raw, output_path)
            return BarcodeResult(barcode, "saved", filename)
        except Exception as exc:
            last_error = str(exc)
    return BarcodeResult(barcode, "error", last_error)


@dataclass
class BarcodeResult:
    """Result of processing a single barcode."""
    barcode: str
    status: Literal["saved", "skipped", "no_image", "error"]
    message: str
