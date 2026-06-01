"""
Site scrapers for price-watch.

Each scraper returns (price, currency, site_display_name, product_name).
The dispatch ``scrape()`` verifies the product name against expected keywords.
"""

from __future__ import annotations

import html as html_mod
import logging
import re

import requests

log = logging.getLogger("aihub.price_watcher")


class PriceScrapeError(Exception):
    """Scrape failed."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux aarch64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }


def _fetch(url: str, timeout: int = 25) -> str:
    try:
        resp = requests.get(url, timeout=timeout, headers=_headers())
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        raise PriceScrapeError(f"HTTP error: {exc}") from exc


def _extract_name(html: str) -> str | None:
    """Extract product name from og:title, then <title>."""
    m = re.search(
        r'<meta\s+[^>]*property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return html_mod.unescape(m.group(1).strip())
    m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if m:
        return html_mod.unescape(m.group(1).strip())
    return None


def _name_matches(name: str | None, keywords: list[str]) -> bool:
    if not name:
        return False
    name_lower = name.lower()
    for kw in keywords:
        if kw.lower() in name_lower:
            return True
    return False


def _extract_product_meta_price(html: str) -> tuple[float, str]:
    """Extract price from product:price:amount / product:price:currency.

    Used by Seeed and TiendaTec (same meta tag pattern).
    """
    m = re.search(
        r'<meta\s+[^>]*property=["\']product:price:amount["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        raise PriceScrapeError("product:price:amount meta tag not found")
    price = float(m.group(1))

    m = re.search(
        r'<meta\s+[^>]*property=["\']product:price:currency["\']\s+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    currency = m.group(1).upper() if m else "EUR"
    return price, currency


# ---------------------------------------------------------------------------
# Per-site scrapers
# ---------------------------------------------------------------------------

def scrape_seeed(url: str, timeout: int = 25) -> tuple[float, str, str, str | None]:
    html = _fetch(url, timeout)
    name = _extract_name(html)
    price, currency = _extract_product_meta_price(html)
    return price, currency, "Seeed Studio", name


def scrape_tiendatec(url: str, timeout: int = 25) -> tuple[float, str, str, str | None]:
    html = _fetch(url, timeout)
    name = _extract_name(html)
    price, currency = _extract_product_meta_price(html)
    return price, currency, "TiendaTec", name


def scrape_amazon(url: str, timeout: int = 30) -> tuple[float, str, str, str | None]:
    """Amazon — price from a-offscreen span, name from productTitle."""
    html = _fetch(url, timeout)
    name = _extract_name(html)

    # Price from a-offscreen span
    m = re.search(
        r'<span[^>]*class="a-offscreen"[^>]*>([^<]+)</span>',
        html,
    )
    if not m:
        raise PriceScrapeError("a-offscreen price span not found")

    raw = m.group(1).strip()
    # Parse "€59.95" or "EUR 59.95" or "$39.99"
    m2 = re.match(r'[^\d]*([\d]+[.,]?\d*)', raw)
    if not m2:
        raise PriceScrapeError(f"Could not parse price from: {raw!r}")

    price = float(m2.group(1).replace(",", ""))

    # Determine currency from symbol or text
    if "€" in raw or "EUR" in raw.upper():
        currency = "EUR"
    elif "$" in raw or "USD" in raw.upper():
        currency = "USD"
    elif "£" in raw or "GBP" in raw.upper():
        currency = "GBP"
    else:
        currency = "EUR"  # default for .es

    return price, currency, "Amazon", name


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

SCRAPERS: dict[str, callable] = {
    "seeed": scrape_seeed,
    "tiendatec": scrape_tiendatec,
    "amazon": scrape_amazon,
}


def scrape(site: str, url: str, keywords: list[str], timeout: int = 25
           ) -> tuple[float, str, str, str | None, bool]:
    """Dispatch to the right scraper, verify product name.

    Returns (price, currency, site_display_name, product_name, name_matched).
    """
    scraper = SCRAPERS.get(site.lower())
    if not scraper:
        raise PriceScrapeError(f"Unknown site: {site!r}")

    price, currency, site_name, product_name = scraper(url, timeout=timeout)
    matched = _name_matches(product_name, keywords)
    return price, currency, site_name, product_name, matched
