"""Find comparable coin information from free public sources.

For V2 we use Wikipedia as the primary source (always accessible, stable URL
patterns, no auth needed). For auction comparables specifically, the right
real source is eBay's sold listings API or Heritage Auctions, both of which
require API keys. We surface a placeholder for those in the output and let
the user opt-in to those providers in V3.

This module is deliberately tolerant: any network failure returns a
"no comparables" result rather than failing the whole scan.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any
import urllib.parse
import httpx

log = logging.getLogger("coinscope.comparables")

WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_HEADERS = {
    "User-Agent": "Coinscope/0.1 (local coin research tool; +https://github.com/local/coinscope)",
    "Accept": "application/json",
}


_WIKI_TITLE_HINTS = {
    # (series_key_lower, year_present) -> canonical Wikipedia page title
    ("lincolnwheatcent", "1909"): "1909-S VDB Lincoln Cent",  # the famous key variety
    ("lincolnwheatcent", "*"): "Lincoln cent",
    ("lincolnmemorialcent", "*"): "Lincoln cent",
    ("indianheadcent", "*"): "Indian Head cent",
    ("jeffersonnickel", "*"): "Jefferson nickel",
    ("rooseveltdime", "*"): "Roosevelt dime",
    ("mercurydime", "*"): "Mercury dime",
    ("washingtonquarter", "*"): "Washington quarter",
    ("walkinglibertyhalf", "*"): "Walking Liberty half dollar",
    ("kennedyhalf", "*"): "Kennedy half dollar",
    ("morgandollar", "*"): "Morgan dollar",
    ("peacedollar", "*"): "Peace dollar",
    ("eisenhowardollar", "*"): "Eisenhower dollar",
    ("susanbanthonydollar", "*"): "Susan B. Anthony dollar",
}


def _wiki_title(identification: dict) -> str | None:
    """Build a plausible Wikipedia page title from the identification."""
    series = (identification.get("series") or "").strip()
    year = (identification.get("year") or "").strip()

    if not series:
        return None

    # Normalize series to our canonical key.
    s = series.lower().replace(" ", "")
    s = s.replace("-", "").replace("penny", "cent")

    # Try the hints table.
    if year:
        key = (s, year)
        if key in _WIKI_TITLE_HINTS:
            return _WIKI_TITLE_HINTS[key]
    key = (s, "*")
    if key in _WIKI_TITLE_HINTS:
        return _WIKI_TITLE_HINTS[key]

    # Fallback: combine year + series as-is.
    return f"{year} {series}".strip()


def _wiki_summary(title: str) -> dict[str, Any] | None:
    """Fetch the Wikipedia summary extract for a page. Returns None on failure."""
    try:
        r = httpx.get(
            WIKI_API.format(title=urllib.parse.quote(title.replace(" ", "_"))),
            headers=WIKI_HEADERS,
            timeout=10.0,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        log.warning("Wikipedia lookup failed for %r: %s", title, e)
        return None


def _extract_mintage(text: str) -> str | None:
    """Pull the first mintage number from a Wikipedia extract."""
    if not text:
        return None
    m = re.search(r"mintage[^0-9]{0,40}([\d,]{4,})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"([\d,]{6,})\s*(?:were\s*)?(?:minted|struck|produced)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def find_comparables(identification: dict, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Look up the identified coin on Wikipedia and pull historical context.
    Returns:
        {
            "wiki_title": "1909 Lincoln Wheat cent",
            "wiki_url": "https://en.wikipedia.org/wiki/...",
            "wiki_extract": "First 2-3 sentence summary...",
            "mintage_from_wiki": "48400000" or None,
            "auction_comparable_sources": [
                {"name": "PCGS Price Guide", "url": "..."},
                {"name": "eBay sold listings", "url": "..."},
            ],
            "sources_checked": ["Wikipedia"],
            "warnings": ["Wikipedia content is general, not condition-specific"],
        }
    """
    title = _wiki_title(identification)
    summary = _wiki_summary(title) if title else None

    wiki_extract = summary.get("extract") if summary else None
    wiki_url = summary.get("content_urls", {}).get("desktop", {}).get("page") if summary else None
    mintage = _extract_mintage(wiki_extract) if wiki_extract else None

    # Auction comparables are user-gated services. Surface useful entry points.
    q = urllib.parse.quote(f"{identification.get('year','')} {identification.get('series','')}".strip())
    auction_sources = [
        {
            "name": "eBay sold listings",
            "url": f"https://www.ebay.com/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1",
            "note": "Filter by 'Sold Items' for actual transaction prices",
        },
        {
            "name": "PCGS Price Guide",
            "url": f"https://www.pcgs.com/prices/coinfacts/",
            "note": "Industry-standard price guide; requires login for full data",
        },
        {
            "name": "NGC Price Guide",
            "url": f"https://www.ngccoin.com/price-guide/united-states/",
            "note": "Companion price guide to PCGS",
        },
        {
            "name": "Heritage Auctions archive",
            "url": f"https://coins.ha.com/c/search-results.zx?keyword={q}",
            "note": "Auction-house archive of realized prices",
        },
    ]

    warnings = []
    if not summary:
        warnings.append(f"Wikipedia summary not available for {title!r}")
    if not mintage:
        warnings.append("Mintage figure not extracted from Wikipedia")
    warnings.append(
        "Comparables are general references, not condition-matched sales. "
        "Real value depends heavily on grade, eye appeal, and provenance."
    )

    return {
        "wiki_title": title,
        "wiki_url": wiki_url,
        "wiki_extract": wiki_extract,
        "mintage_from_wiki": mintage,
        "auction_comparable_sources": auction_sources,
        "sources_checked": ["Wikipedia"],
        "warnings": warnings,
    }