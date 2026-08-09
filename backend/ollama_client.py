"""Ollama client. Wraps vision model + structured coin identification."""
from __future__ import annotations
import base64
import io
import json
import re
from typing import Any
import httpx

from . import config


class OllamaError(Exception):
    pass


def _image_to_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


# Identification prompt. Asks the model to extract structured fields then output JSON.
# Note: small models like moondream 1B often can't reliably produce structured JSON,
# so we use a generous prompt and a tolerant JSON parser.
_IDENTIFY_SYSTEM = (
    "You are a coin identification expert with knowledge of US and world coinage. "
    "You will be shown photographs of coins. Your job is to extract identifying "
    "information and return ONLY valid JSON, no markdown fences, no commentary, "
    "no prose before or after the JSON. If a field is unreadable, use null. "
    "Do not guess values you cannot see. Pay special attention to mint marks, "
    "designer initials (like VDB on Lincoln cents), and any doubled-letter or "
    "doubled-date evidence — these can indicate valuable varieties."
)

_IDENTIFY_PROMPT_TEMPLATE = """Look at this photograph of a coin.

Return JSON with these exact keys (do not copy the example values literally — fill in what you actually see in the image):

{{
  "country": "United States" or null,
  "denomination": "One Cent" or null,
  "series": "Lincoln Wheat Cent" or null,
  "year": "1909" or null,
  "mint_mark": "D" or null,
  "is_obverse": true,
  "obverse_description": "brief description of what is on the obverse",
  "reverse_description": "brief description of what is on the reverse if visible, else empty string",
  "composition": "copper" or "silver" or "cupronickel" or "gold" or "brass" or null,
  "diameter_estimate_mm": 24,
  "visible_text": ["LIBERTY", "IN GOD WE TRUST"],
  "image_quality": {{"sharp": true, "well_lit": true, "obstructed": false}},
  "notes": "any uncertainty or observations"
}}

Return only the JSON object. Do not include arrays of placeholder text."""


def _strip_fences(text: str) -> str:
    """Remove markdown code fences and any prose around the JSON object."""
    text = text.strip()
    # Remove leading/trailing fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find the first { and last }
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        return text[a : b + 1]
    return text


def _parse_ident_json(text: str) -> dict[str, Any]:
    """Tolerant JSON parser for small-vision-model output.

    Tries (in order):
      1. Strict parse of the cleaned text.
      2. If the model wrapped the object in a list, extract the first element.
      3. Find the outermost balanced {...} block and parse that.
      4. Try to repair common formatting issues (trailing commas, doubled braces).
    """
    cleaned = _strip_fences(text)
    # 1. Strict parse
    try:
        return _unwrap(json.loads(cleaned))
    except json.JSONDecodeError:
        pass
    # 2. Balanced-brace extraction (handles nested objects correctly).
    candidate = _find_outer_json(cleaned)
    if candidate:
        # Repair doubled braces {{ }} -> { } (llava's quirk).
        repaired = re.sub(r"\{\{", "{", candidate)
        repaired = re.sub(r"\}\}", "}", repaired)
        # Repair trailing commas before closing.
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            return _unwrap(json.loads(repaired))
        except json.JSONDecodeError:
            pass
    raise OllamaError(f"No JSON found in model output: {text[:300]}")


def _find_outer_json(text: str) -> str | None:
    """Find the first balanced {...} block (handles one level of nesting).
    Returns the substring, or None if not found."""
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
    """If the model returned a list of one dict, return that dict.
    If it's a dict with a single key whose value is a dict, unwrap that.
    Otherwise return as-is (must be a dict)."""
    if isinstance(value, list):
        if not value:
            raise OllamaError("Model returned an empty JSON array")
        if not isinstance(value[0], dict):
            raise OllamaError(f"Model returned array of {type(value[0]).__name__}, not dict")
        return value[0]
    if isinstance(value, dict):
        return value
    raise OllamaError(f"Model returned {type(value).__name__}, not dict")


def identify_coin(image_bytes: bytes, *, timeout_s: float = 120.0) -> dict[str, Any]:
    """Call the local vision model to identify a coin from a photo.
    Returns the parsed JSON dict. Raises OllamaError on transport/parse failures.
    """
    img_b64 = _image_to_b64(image_bytes)
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _IDENTIFY_SYSTEM},
            {
                "role": "user",
                "content": _IDENTIFY_PROMPT_TEMPLATE,
                "images": [img_b64],
            },
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    try:
        resp = httpx.post(
            f"{config.OLLAMA_BASE}/api/chat",
            json=body,
            timeout=timeout_s,
        )
    except httpx.HTTPError as e:
        raise OllamaError(f"Ollama transport error: {e}") from e
    if resp.status_code != 200:
        raise OllamaError(
            f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json()
    text = data.get("message", {}).get("content", "")
    if not text:
        raise OllamaError(f"Ollama returned empty content: {data}")
    return _parse_ident_json(text)


def is_ollama_alive() -> bool:
    """Cheap liveness check for /health."""
    try:
        r = httpx.get(f"{config.OLLAMA_BASE}/api/tags", timeout=3.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False