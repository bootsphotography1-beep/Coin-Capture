"""Rarity research. For non-canonical coins, falls through to a second LLM call
that synthesizes what the model knows about mintage, varieties, and value ranges.
Honest about its limits — no web access here, so it relies on model knowledge.
"""
from __future__ import annotations
import json
import re
from typing import Any
import httpx

import config


_RARITY_SYSTEM = (
    "You are a numismatist with deep knowledge of US and world coins. "
    "Given a coin identification, return a structured rarity assessment. "
    "Return ONLY valid JSON, no markdown fences. Be honest about uncertainty — "
    "if you don't know a value, return null rather than guess. Always include "
    "a 'value_note' explaining the limits of the estimate."
)

_RARITY_PROMPT_TEMPLATE = """Given this coin identification:
{identification}

Return JSON with these exact keys:
{{
  "name": "string",                       // e.g. "1916 Standing Liberty Quarter"
  "mintage": number or null,              // total production if known, else null
  "rarity_tier": "common" | "semi-key" | "key" | "rare" | "unknown",
  "scarcity_note": "string",              // 1-2 sentence plain English explanation
  "estimated_value_low_usd": number or null,
  "estimated_value_high_usd": number or null,
  "value_note": "string",                 // honest caveats — circulated grade assumed, errors can multiply value, etc.
  "known_varieties": ["string", ...],     // notable varieties/errors if any
  "authentication_advice": "string"       // when professional authentication is recommended
}}

Be conservative with values. If the coin's value depends heavily on condition,
say so. If a key date/variety exists, mention it. Return only the JSON object."""


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        return text[a : b + 1]
    return text


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = _strip_fences(text)
    try:
        return _unwrap(json.loads(cleaned))
    except json.JSONDecodeError:
        pass
    candidate = _find_outer_json(cleaned)
    if candidate:
        repaired = re.sub(r"\{\{", "{", candidate)
        repaired = re.sub(r"\}\}", "}", repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return _unwrap(json.loads(repaired))
    raise ValueError(f"No JSON in rarity output: {text[:200]}")


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


def _unwrap(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        if not value or not isinstance(value[0], dict):
            raise ValueError("Model returned non-object array")
        return value[0]
    if isinstance(value, dict):
        return value
    raise ValueError(f"Model returned {type(value).__name__}, not dict")


def research_rarity(identification: dict, *, timeout_s: float = 120.0) -> dict[str, Any]:
    """Call the local model to generate a rarity report from the identification.
    Returns the parsed JSON dict. Honest defaults on failure.
    """
    prompt = _RARITY_PROMPT_TEMPLATE.format(identification=json.dumps(identification, indent=2))
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _RARITY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        resp = httpx.post(
            f"{config.OLLAMA_BASE}/api/chat",
            json=body,
            timeout=timeout_s,
        )
    except httpx.HTTPError as e:
        return _fallback_report(identification, error=f"Transport error: {e}")
    if resp.status_code != 200:
        return _fallback_report(identification, error=f"HTTP {resp.status_code}")
    data = resp.json()
    text = data.get("message", {}).get("content", "")
    if not text:
        return _fallback_report(identification, error="Empty model response")
    try:
        report = _parse_json(text)
        report["source"] = "model"
        return report
    except Exception as e:
        return _fallback_report(identification, error=f"Parse error: {e}")


def _fallback_report(identification: dict, error: str | None = None) -> dict:
    """What to return when the model fails — still honest, just less informative."""
    return {
        "source": "fallback",
        "name": identification.get("series") or "Unknown coin",
        "mintage": None,
        "rarity_tier": "unknown",
        "scarcity_note": "Could not retrieve rarity data from the local model.",
        "estimated_value_low_usd": None,
        "estimated_value_high_usd": None,
        "value_note": "No automated estimate available. Consult a professional numismatist.",
        "known_varieties": [],
        "authentication_advice": "Professional authentication recommended.",
        "error": error,
    }