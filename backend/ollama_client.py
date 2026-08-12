"""Ollama client. Vision identification tuned for US pocket change on a local GPU.

Latency knobs (aimed at ~3–4s identify on an RTX 4070 Ti):
  - Cropped coin already fills the frame (caller should crop first)
  - Downscale to MODEL_MAX_DIM before upload to Ollama
  - Small num_ctx / num_predict (qwen2.5vl defaults to a huge context)
  - keep_alive so the model stays in VRAM between scans
  - JSON mode + a short US-only prompt
"""
from __future__ import annotations
import json
import re
from typing import Any
import httpx

from . import config
from . import crop


class OllamaError(Exception):
    pass


_IDENTIFY_SYSTEM = (
    "You identify United States circulating coins from photographs. "
    "Return ONLY a JSON object. No markdown. No extra keys. "
    "If a field is unreadable, use null. Do not guess a year or mint mark you cannot see."
)

_IDENTIFY_PROMPT = """This photo is a cropped US coin (pocket change). Read the date and mint mark carefully.

Return JSON:
{"country":"United States","denomination":"One Cent|Five Cents|Dime|Quarter Dollar|Half Dollar|One Dollar or null","series":"Lincoln Shield Cent|Lincoln Memorial Cent|Lincoln Wheat Cent|Jefferson Nickel|Roosevelt Dime|Washington Quarter|Kennedy Half Dollar|or the true series","year":"YYYY or null","mint_mark":"P|D|S|W|empty string if none visible","composition":"copper|zinc|cupronickel|silver|clad|null","visible_text":["..."],"notes":"short"}
"""


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


def _unwrap(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        if not value:
            raise OllamaError("Model returned an empty JSON array")
        if not isinstance(value[0], dict):
            raise OllamaError(f"Model returned array of {type(value[0]).__name__}, not dict")
        return value[0]
    if isinstance(value, dict):
        return value
    raise OllamaError(f"Model returned {type(value).__name__}, not dict")


def _parse_ident_json(text: str) -> dict[str, Any]:
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
        try:
            return _unwrap(json.loads(repaired))
        except json.JSONDecodeError:
            pass
    raise OllamaError(f"No JSON found in model output: {text[:300]}")


def identify_coin(image_bytes: bytes, *, timeout_s: float | None = None) -> dict[str, Any]:
    timeout_s = timeout_s if timeout_s is not None else config.IDENTIFY_TIMEOUT_S
    small = crop.downscale_for_model(image_bytes, max_dim=config.MODEL_MAX_DIM)
    img_b64 = __import__("base64").b64encode(small).decode("ascii")
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _IDENTIFY_SYSTEM},
            {
                "role": "user",
                "content": _IDENTIFY_PROMPT,
                "images": [img_b64],
            },
        ],
        "stream": False,
        "format": "json",
        "keep_alive": "60m",
        "options": {
            "temperature": 0.0,
            "num_ctx": 2048,
            "num_predict": 180,
        },
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
    parsed = _parse_ident_json(text)
    # Normalize year to a 4-digit string when possible.
    year = parsed.get("year")
    if year is not None:
        parsed["year"] = str(year).strip()[:4] or None
    mm = parsed.get("mint_mark")
    if isinstance(mm, str):
        parsed["mint_mark"] = mm.strip().upper()[:2] or None
    return parsed


def is_ollama_alive() -> bool:
    try:
        r = httpx.get(f"{config.OLLAMA_BASE}/api/tags", timeout=3.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def list_models() -> list[str]:
    try:
        r = httpx.get(f"{config.OLLAMA_BASE}/api/tags", timeout=5.0)
        if r.status_code != 200:
            return []
        return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except httpx.HTTPError:
        return []
