"""Canonical US coin DB. Used as a short-circuit: if the LLM identifies a coin
that matches a known entry, we serve mintage/scarcity data from here without
running a second model call. The fallback path ('unknown coin') triggers an
LLM-based rarity assessment.

This is intentionally small and conservative — only includes coins common
enough that someone scanning a pocket-change collection would plausibly hit.
Errors/varieties are NOT in here; those always go through the LLM path.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import config


def _normalize(s: str) -> str:
    return (s or "").lower().strip().replace(" ", "").replace("-", "")


# US coins keyed by (series_key, year). year may be None for "any year of this series".
# Mintage figures are production totals (Philadelphia + branch mints combined where applicable).
# Sources: PCGS price guide & Red Book for common issues. Numbers rounded.
_US_COINS: list[dict[str, Any]] = [
    # --- Lincoln Wheat Cent (1909-1958) — common key dates ---
    {
        "key": ("lincolnwheatcent", "1909"),
        "name": "1909 Lincoln Wheat Cent (VDB)",
        "series": "Lincoln Wheat Cent",
        "year": "1909",
        "mintage": 48400000,  # 1909 with VDB is the famous variety; the no-VDB is ~484M but with VDB is ~484M total with subset
        "rarity_tier": "semi-key",  # VDB on reverse is a premium variety
        "scarcity_note": "Common date but the VDB designer initials are a popular variety; value depends on VDB vs no-VDB and condition.",
        "common_value_low": 5.0,
        "common_value_high": 50.0,
    },
    {
        "key": ("lincolnwheatcent", "1914"),
        "name": "1914 Lincoln Wheat Cent",
        "series": "Lincoln Wheat Cent",
        "year": "1914",
        "mintage": 75220000,
        "rarity_tier": "key",
        "scarcity_note": "Lower mintage year. Value rises sharply in uncirculated condition.",
        "common_value_low": 1.0,
        "common_value_high": 200.0,
    },
    {
        "key": ("lincolnwheatcent", "1922"),
        "name": "1922 Lincoln Wheat Cent (Plain)",
        "series": "Lincoln Wheat Cent",
        "year": "1922",
        "mintage": 7000000,  # plain variety only; total 1922 mintage is much higher
        "rarity_tier": "key",
        "scarcity_note": "The 1922 'plain' (no D mint mark) is the well-known error variety. Requires authentication.",
        "common_value_low": 25.0,
        "common_value_high": 1500.0,
    },
    {
        "key": ("lincolnwheatcent", "1931"),
        "name": "1931 Lincoln Wheat Cent",
        "series": "Lincoln Wheat Cent",
        "year": "1931",
        "mintage": 19560000,
        "rarity_tier": "semi-key",
        "scarcity_note": "Lowest mintage of the series. Premium in uncirculated grades.",
        "common_value_low": 1.0,
        "common_value_high": 80.0,
    },
    # Generic Lincoln Wheat Cent fallback (any other year)
    {
        "key": ("lincolnwheatcent", None),
        "name": "Lincoln Wheat Cent",
        "series": "Lincoln Wheat Cent",
        "year": None,
        "mintage": 50000000,
        "rarity_tier": "common",
        "scarcity_note": "Wheat cents (1909-1958) are common in lower grades. Value hinges on date, mint mark, and condition.",
        "common_value_low": 0.05,
        "common_value_high": 5.0,
    },
    # --- Lincoln Memorial Cent (1959-2008) ---
    {
        "key": ("lincolnmemorialcent", "1982"),
        "name": "1982 Lincoln Memorial Cent (zinc vs copper transition)",
        "series": "Lincoln Memorial Cent",
        "year": "1982",
        "mintage": 10000000000,
        "rarity_tier": "common",
        "scarcity_note": "1982 cents come in copper (small date) and zinc (large date) varieties; the copper small-date variety carries a premium.",
        "common_value_low": 0.05,
        "common_value_high": 50.0,
    },
    {
        "key": ("lincolnmemorialcent", None),
        "name": "Lincoln Memorial Cent",
        "series": "Lincoln Memorial Cent",
        "year": None,
        "mintage": 5000000000,
        "rarity_tier": "common",
        "scarcity_note": "Commonly found in circulation. Value primarily from proof or uncirculated rolls.",
        "common_value_low": 0.01,
        "common_value_high": 1.0,
    },
    # --- Indian Head Cent (1859-1909) ---
    {
        "key": ("indianheadcent", None),
        "name": "Indian Head Cent",
        "series": "Indian Head Cent",
        "year": None,
        "mintage": 25000000,
        "rarity_tier": "common",
        "scarcity_note": "Mostly common in lower grades. Key dates (1877, 1909-S, 1908-S) carry significant premiums.",
        "common_value_low": 1.0,
        "common_value_high": 100.0,
    },
    # --- Jefferson Nickel (1938-present) ---
    {
        "key": ("jeffersonnickel", "1939"),
        "name": "1939 Jefferson Nickel",
        "series": "Jefferson Nickel",
        "year": "1939",
        "mintage": 120615000,
        "rarity_tier": "common",
        "scarcity_note": "First year of issue. Common date but worth premium in uncirculated or with full steps.",
        "common_value_low": 0.5,
        "common_value_high": 20.0,
    },
    {
        "key": ("jeffersonnickel", "1950"),
        "name": "1950 Jefferson Nickel",
        "series": "Jefferson Nickel",
        "year": "1950",
        "mintage": 55632000,
        "rarity_tier": "common",
        "scarcity_note": "Common. Premium for full-steps designation.",
        "common_value_low": 0.1,
        "common_value_high": 15.0,
    },
    {
        "key": ("jeffersonnickel", None),
        "name": "Jefferson Nickel",
        "series": "Jefferson Nickel",
        "year": None,
        "mintage": 100000000,
        "rarity_tier": "common",
        "scarcity_note": "Common in circulation. War nickels (1942-1945, 35% silver) carry intrinsic and numismatic premium.",
        "common_value_low": 0.05,
        "common_value_high": 5.0,
    },
    # --- Roosevelt Dime (1946-present) ---
    {
        "key": ("rooseveltdime", "1946"),
        "name": "1946 Roosevelt Dime",
        "series": "Roosevelt Dime",
        "year": "1946",
        "mintage": 255400000,
        "rarity_tier": "common",
        "scarcity_note": "First year of issue. Common.",
        "common_value_low": 1.0,
        "common_value_high": 8.0,
    },
    {
        "key": ("rooseveltdime", None),
        "name": "Roosevelt Dime",
        "series": "Roosevelt Dime",
        "year": None,
        "mintage": 200000000,
        "rarity_tier": "common",
        "scarcity_note": "Common. Pre-1965 are 90% silver and carry melt premium.",
        "common_value_low": 0.1,
        "common_value_high": 3.0,
    },
    # --- Mercury Dime (1916-1945) ---
    {
        "key": ("mercurydime", "1916"),
        "name": "1916-D Mercury Dime",
        "series": "Mercury Dime",
        "year": "1916",
        "mintage": 264000,
        "rarity_tier": "key",
        "scarcity_note": "Lowest mintage of the Mercury series. Premium coin.",
        "common_value_low": 500.0,
        "common_value_high": 5000.0,
    },
    {
        "key": ("mercurydime", None),
        "name": "Mercury Dime",
        "series": "Mercury Dime",
        "year": None,
        "mintage": 50000000,
        "rarity_tier": "semi-key",
        "scarcity_note": "Mostly common. 90% silver. Key dates (1916-D, 1921, 1921-D) carry strong premiums.",
        "common_value_low": 1.0,
        "common_value_high": 50.0,
    },
    # --- Washington Quarter (1932-present) ---
    {
        "key": ("washingtonquarter", "1932"),
        "name": "1932 Washington Quarter",
        "series": "Washington Quarter",
        "year": "1932",
        "mintage": 5402000,
        "rarity_tier": "key",
        "scarcity_note": "First year of issue, low mintage. Premium in uncirculated.",
        "common_value_low": 5.0,
        "common_value_high": 200.0,
    },
    {
        "key": ("washingtonquarter", None),
        "name": "Washington Quarter",
        "series": "Washington Quarter",
        "year": None,
        "mintage": 100000000,
        "rarity_tier": "common",
        "scarcity_note": "Common in circulation. Pre-1965 are 90% silver. State quarters (1999-2008) carry small premiums in proof or silver proof.",
        "common_value_low": 0.25,
        "common_value_high": 5.0,
    },
    # --- Walking Liberty Half Dollar (1916-1947) ---
    {
        "key": ("walkinglibertyhalf", "1921"),
        "name": "1921 Walking Liberty Half Dollar",
        "series": "Walking Liberty Half Dollar",
        "year": "1921",
        "mintage": 1000000,  # rough; multiple mints, 1921 is key
        "rarity_tier": "key",
        "scarcity_note": "Key date for the Walking Liberty series.",
        "common_value_low": 50.0,
        "common_value_high": 2000.0,
    },
    {
        "key": ("walkinglibertyhalf", None),
        "name": "Walking Liberty Half Dollar",
        "series": "Walking Liberty Half Dollar",
        "year": None,
        "mintage": 5000000,
        "rarity_tier": "semi-key",
        "scarcity_note": "90% silver. Key dates (1921, 1921-D, 1921-S) carry strong premiums.",
        "common_value_low": 5.0,
        "common_value_high": 200.0,
    },
    # --- Kennedy Half Dollar (1964-present) ---
    {
        "key": ("kennedyhalf", "1964"),
        "name": "1964 Kennedy Half Dollar",
        "series": "Kennedy Half Dollar",
        "year": "1964",
        "mintage": 273400000,
        "rarity_tier": "common",
        "scarcity_note": "Only 90% silver Kennedy half. Common but carries silver premium.",
        "common_value_low": 5.0,
        "common_value_high": 15.0,
    },
    {
        "key": ("kennedyhalf", None),
        "name": "Kennedy Half Dollar",
        "series": "Kennedy Half Dollar",
        "year": None,
        "mintage": 50000000,
        "rarity_tier": "common",
        "scarcity_note": "Common. 1964 is silver; 1965-1970 are 40% silver clad; 1971+ are copper-nickel.",
        "common_value_low": 0.5,
        "common_value_high": 10.0,
    },
    # --- Morgan Dollar (1878-1921) ---
    {
        "key": ("morgandollar", "1893"),
        "name": "1893-S Morgan Dollar",
        "series": "Morgan Dollar",
        "year": "1893",
        "mintage": 100000,  # 1893-S is the key Morgan date
        "rarity_tier": "key",
        "scarcity_note": "Lowest mintage Morgan. Strong premium in any grade.",
        "common_value_low": 1000.0,
        "common_value_high": 25000.0,
    },
    {
        "key": ("morgandollar", None),
        "name": "Morgan Dollar",
        "series": "Morgan Dollar",
        "year": None,
        "mintage": 5000000,
        "rarity_tier": "semi-key",
        "scarcity_note": "90% silver. Common dates trade near silver melt; key dates (1893-S, 1889-CC, 1892-S, 1895-S, 1884-CC) carry strong premiums.",
        "common_value_low": 25.0,
        "common_value_high": 200.0,
    },
    # --- Peace Dollar (1921-1935) ---
    {
        "key": ("peacedollar", "1928"),
        "name": "1928 Peace Dollar",
        "series": "Peace Dollar",
        "year": "1928",
        "mintage": 360649,
        "rarity_tier": "key",
        "scarcity_note": "Lowest mintage Peace dollar. Strong premium.",
        "common_value_low": 200.0,
        "common_value_high": 1500.0,
    },
    {
        "key": ("peacedollar", None),
        "name": "Peace Dollar",
        "series": "Peace Dollar",
        "year": None,
        "mintage": 4000000,
        "rarity_tier": "semi-key",
        "scarcity_note": "90% silver. 1928 is the key date. Common dates carry modest premium over silver melt.",
        "common_value_low": 25.0,
        "common_value_high": 200.0,
    },
    # --- Eisenhower Dollar (1971-1978) ---
    {
        "key": ("eisenhowardollar", None),
        "name": "Eisenhower Dollar",
        "series": "Eisenhower Dollar",
        "year": None,
        "mintage": 30000000,
        "rarity_tier": "common",
        "scarcity_note": "Commonly traded. 40% silver clad for special mint issues (1971-S, 1972-S, 1973-S, 1974-S) carry premium.",
        "common_value_low": 1.0,
        "common_value_high": 30.0,
    },
    # --- Susan B Anthony Dollar (1979-1981, 1999) ---
    {
        "key": ("susanbanthonydollar", None),
        "name": "Susan B. Anthony Dollar",
        "series": "Susan B. Anthony Dollar",
        "year": None,
        "mintage": 200000000,
        "rarity_tier": "common",
        "scarcity_note": "Common. Generally worth face value.",
        "common_value_low": 1.0,
        "common_value_high": 5.0,
    },
]


# Aliases to help match LLM output to canonical entries.
_SERIES_ALIASES: dict[str, str] = {
    "lincolnwheatcent": "lincolnwheatcent",
    "lincolnwheat": "lincolnwheatcent",
    "wheatcent": "lincolnwheatcent",
    "wheatpenny": "lincolnwheatcent",
    "wheatcentback": "lincolnwheatcent",
    "lincolnmemorialcent": "lincolnmemorialcent",
    "lincolnmemorial": "lincolnmemorialcent",
    "memorialcent": "lincolnmemorialcent",
    "indianheadcent": "indianheadcent",
    "indianhead": "indianheadcent",
    "indianheadpenny": "indianheadcent",
    "jeffersonnickel": "jeffersonnickel",
    "jeffnickel": "jeffersonnickel",
    "jeff": "jeffersonnickel",
    "rooseveltdime": "rooseveltdime",
    "roosevelt": "rooseveltdime",
    "mercurydime": "mercurydime",
    "mercury": "mercurydime",
    "mercdime": "mercurydime",
    "washingtonquarter": "washingtonquarter",
    "washington": "washingtonquarter",
    "walkinglibertyhalf": "walkinglibertyhalf",
    "walkingliberty": "walkinglibertyhalf",
    "walkerlady": "walkinglibertyhalf",
    "kennedyhalf": "kennedyhalf",
    "kennedyhalfdollar": "kennedyhalf",
    "morgandollar": "morgandollar",
    "morgan": "morgandollar",
    "peacedollar": "peacedollar",
    "peace": "peacedollar",
    "eisenhowardollar": "eisenhowardollar",
    "eisenhower": "eisenhowardollar",
    "ike": "eisenhowardollar",
    "susanbanthonydollar": "susanbanthonydollar",
    "susanbanthony": "susanbanthonydollar",
    "sba": "susanbanthonydollar",
}


def _series_key(series: str | None) -> str | None:
    if not series:
        return None
    return _SERIES_ALIASES.get(_normalize(series))


def lookup_canonical(identification: dict) -> dict | None:
    """Try to find a canonical entry matching this LLM identification.
    Returns the rarity dict (with name, mintage, scarcity_note, value range) or None.
    The None path triggers a follow-up LLM call for the rarity report.
    """
    series = identification.get("series") or identification.get("denomination") or ""
    year = str(identification.get("year") or "").strip()
    sk = _series_key(series)
    if not sk:
        return None
    # Try exact year match first
    for entry in _US_COINS:
        if entry["key"][0] == sk and entry["key"][1] == year:
            return _entry_to_report(entry)
    # Fall back to series-level (any year) entry
    for entry in _US_COINS:
        if entry["key"][0] == sk and entry["key"][1] is None:
            return _entry_to_report(entry)
    return None


def _entry_to_report(entry: dict) -> dict:
    """Convert a canonical DB entry into a rarity report dict."""
    return {
        "source": "canonical",
        "matched_series": entry["series"],
        "matched_year": entry["year"],
        "name": entry["name"],
        "mintage": entry["mintage"],
        "rarity_tier": entry["rarity_tier"],
        "scarcity_note": entry["scarcity_note"],
        "estimated_value_low_usd": entry["common_value_low"],
        "estimated_value_high_usd": entry["common_value_high"],
        "value_note": (
            "Range assumes typical circulated grade. "
            "Uncirculated or graded examples can be multiples higher. "
            "These are screening estimates, not appraisals."
        ),
    }


def export_canonical() -> dict:
    """Expose the canonical list for the dashboard to show in the UI."""
    return {
        "series_count": len({e["key"][0] for e in _US_COINS}),
        "entries": [
            {"series": e["series"], "year": e["year"], "rarity_tier": e["rarity_tier"]}
            for e in _US_COINS
        ],
    }