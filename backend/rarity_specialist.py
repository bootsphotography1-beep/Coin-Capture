"""Second-stage rarity specialist.

After llava:7b identifies the coin, this module consults a *separate* model
(text-only, factual-recall oriented) to assess rarity and value. The point
of using a different model:

  - llava is good at vision, weak at structured numismatic knowledge
  - llama3.1:8b (or similar text-only Llama) is the opposite — no vision,
    but stronger factual recall about mintage figures, varieties, and
    auction-realized prices because it was trained on more general text data

We use the canonical DB first (instant, deterministic, correct for the
common ~25 entries we have), and only call this specialist when the
canonical DB missed AND llava's identification has the key fields we need
(series + year at minimum).

Like ollama_client.py, JSON output is parsed with the same tolerant parser.
The specialist prompt explicitly forbids re-identifying the coin — it only
rates rarity/value based on the identification we hand it.
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Any
import httpx

from . import config

log = logging.getLogger("coinscope.rarity_specialist")

# Default to llama3.1:8b (text-only, fast, good at factual recall).
# Can be overridden with COINSCOPE_RARITY_MODEL env var.
RARITY_MODEL = getattr(config, "RARITY_MODEL", None) or "llama3.1:8b"

_RARITY_SYSTEM = (
    "You are a numismatist with deep knowledge of US and world coinage. "
    "Given a coin identification, return a rarity assessment as JSON only. "
    "Do NOT re-identify the coin — the identification has already been done. "
    "Focus only on rarity, mintage, varieties, and value range. Be honest: "
    "if you don't know a value, return null. Use the same JSON shape every time."
)

_RARITY_PROMPT_TEMPLATE = """A vision model has identified this coin:

{identification}

Return ONLY a JSON object with these exact keys:

{{
  "name": "string",                              // specific catalog name, e.g. "1909-S VDB Lincoln Cent"
  "mintage": number or null,                     // total production if known
  "rarity_tier": "common" | "semi-key" | "key" | "rare" | "unknown",
  "scarcity_note": "string",                     // 1-2 sentences explaining the rarity position
  "estimated_value_low_usd": number or null,
  "estimated_value_high_usd": number or null,
  "value_note": "string",                        // honest caveats — condition assumed circulated, errors can multiply value, etc.
  "known_varieties": ["string", ...],            // notable varieties or errors (empty list if none)
  "authentication_advice": "string"             // when professional authentication is recommended
}}

Rarity tier definitions:
- "common": mintage in the hundreds of millions, found in circulation
- "semi-key": mintage in the low millions or fewer, premium in uncirculated
- "key": mintage under 1 million, always carries meaningful premium
- "rare": mintage under 100k, often requires authentication
- "unknown": you don't have reliable knowledge of this coin's rarity

Value ranges: assume circulated grade (the typical pocket-change condition).
For key dates in uncirculated condition, values can be 5-50x higher — say so
in value_note. If the coin's value depends heavily on a variety or error,
mention it in known_varieties.

Return only the JSON object."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        return text[a : b + 1]
    return text


def _find_outer_json(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = _strip_fences(text)
    # Repair common LLM JSON mistakes:
    #   1. Thousands-separator commas inside numbers: 1,120,000 -> 1120000
    #   2. Trailing commas before closing braces/brackets
    #   3. Stray markdown fence artifacts after stripping
    #   4. Invalid backslash escapes (\' leaking from Python-style strings)
    repaired = re.sub(r'(?<=[\d]),(?=[\d])', '', cleaned)
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)
    repaired = re.sub(r'\{\{', '{', repaired)
    repaired = re.sub(r'\}\}', '}', repaired)
    repaired = re.sub(r"\\'", "'", repaired)
    repaired = re.sub(r'\\([^"\\/bfnrtu])', r'\1', repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    candidate = _find_outer_json(repaired)
    if candidate:
        return json.loads(candidate)
    raise ValueError(f"No JSON in specialist output: {text[:200]}")


def has_enough_info_for_specialist(identification: dict) -> bool:
    """Decide whether the specialist is worth calling.

    The specialist can produce useful output even without a precise mint mark,
    but it needs at least a series (so it knows what coin to research) and
    ideally a year (so it knows which date within the series).
    Returns False when the identification is too thin to be useful.
    """
    series = (identification.get("series") or "").strip()
    year = str(identification.get("year") or "").strip()
    if not series:
        return False
    # Without a year, the specialist will say "depends on the date" — useful
    # but only marginally. Still allow it.
    return True


def research_rarity_specialist(identification: dict, *, timeout_s: float = 180.0) -> dict[str, Any]:
    """Call the rarity-specialist model. Returns the parsed JSON dict.

    Raises httpx.HTTPError on transport failure, ValueError on parse failure.
    Caller is expected to handle both gracefully (fall back to the generic
    rarity.py path or a "could not research" message).

    Timeout default is 180s because llama3.1:8b cold-loads its 4.9GB weights
    on first use after Ollama restart — that load alone takes 20–40s on
    Windows, plus 5–15s for the actual generation. 60s was too tight and
    caused silent fallback to the generic path on cold starts.
    """
    # Use ensure_ascii=False so non-ASCII chars (em-dashes, accented letters,
    # diacritics on foreign coin legends) stay as readable Unicode rather than
    # \uXXXX escapes. Two reasons:
    #   1. The LLM sees a more natural prompt and produces cleaner output
    #   2. \uXXXX in the prompt sometimes bleeds into the LLM's JSON output,
    #      producing malformed escapes (e.g. \\u emitting a literal backslash
    #      sequence the JSON parser can't decode).
    ident_text = json.dumps(identification, indent=2, ensure_ascii=False)
    prompt = _RARITY_PROMPT_TEMPLATE.format(identification=ident_text)
    body = {
        "model": RARITY_MODEL,
        "messages": [
            {"role": "system", "content": _RARITY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    log.info("Calling rarity specialist model=%s timeout=%.0fs", RARITY_MODEL, timeout_s)
    t0 = time.monotonic()
    resp = httpx.post(
        f"{config.OLLAMA_BASE}/api/chat",
        json=body,
        timeout=timeout_s,
    )
    if resp.status_code != 200:
        raise httpx.HTTPError(f"Specialist returned HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    text = data.get("message", {}).get("content", "")
    elapsed = time.monotonic() - t0
    log.info("Specialist responded in %.1fs, %d chars", elapsed, len(text))
    if not text:
        raise ValueError("Specialist returned empty content")
    parsed = _parse_json(text)
    parsed["source"] = "specialist"
    parsed["specialist_model"] = RARITY_MODEL
    parsed["specialist_elapsed_s"] = round(elapsed, 1)
    return parsed