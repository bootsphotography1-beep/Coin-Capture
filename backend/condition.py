"""Condition estimation from a coin photo.

We can't truly grade a coin from a photo, but we can extract quantitative
signals that correlate with coin condition: edge sharpness, contrast,
wear-detection heuristics (high-frequency texture loss on the relief),
and luma distribution.

Returns a 0-100 numeric score plus a verbal band (Poor/Fine/EF/AU/MS/etc.)
plus the contributing signals so the user understands what the score means.

Honest about its limits: a real grade requires physical inspection, weight,
and known authenticators. Use this as a screening hint, not an appraisal.
"""
from __future__ import annotations
import io
from typing import Any
from PIL import Image


def _to_grayscale(img: Image.Image) -> Image.Image:
    return img.convert("L") if img.mode != "L" else img


def _resize_for_analysis(img: Image.Image, max_dim: int = 512) -> Image.Image:
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _luma_histogram(gray: Image.Image) -> dict[str, float]:
    """Compute luma distribution statistics."""
    hist = gray.histogram()
    total = sum(hist) or 1
    # Mean luma
    mean = sum(i * v for i, v in enumerate(hist)) / total
    # Standard deviation (proxy for contrast)
    var = sum(((i - mean) ** 2) * v for i, v in enumerate(hist)) / total
    std = var ** 0.5
    # Fraction of pixels in dark / mid / bright bands
    dark = sum(hist[:64]) / total
    mid = sum(hist[64:192]) / total
    bright = sum(hist[192:]) / total
    return {
        "mean": mean / 255,
        "std": std / 127.5,
        "dark_frac": dark,
        "mid_frac": mid,
        "bright_frac": bright,
    }


def _sharpness(gray: Image.Image) -> float:
    """Laplacian-variance approximation. Higher = sharper focus / more detail."""
    import math
    px = gray.load()
    w, h = gray.size
    if w < 3 or h < 3:
        return 0.0
    # Downsample for speed.
    step_x = max(1, w // 256)
    step_y = max(1, h // 256)
    diffs = []
    for y in range(1, h - 1, step_y):
        for x in range(1, w - 1, step_x):
            c = px[x, y]
            up = px[x, y - 1]
            down = px[x, y + 1]
            left = px[x - 1, y]
            right = px[x + 1, y]
            lap = abs(4 * c - up - down - left - right)
            if lap > 0:
                diffs.append(lap)
    if not diffs:
        return 0.0
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    return var / 1000.0  # normalize to a friendlier range


def _wear_signal(gray: Image.Image) -> float:
    """Higher-frequency energy within bright regions = preserved detail (less worn).
    Worn coins have smoother highlights; well-preserved coins have crisp details.
    Returns 0 (heavily worn) to 1 (sharp detail)."""
    px = gray.load()
    w, h = gray.size
    if w < 8 or h < 8:
        return 0.0
    step_x = max(1, w // 200)
    step_y = max(1, h // 200)
    bright_diffs = []
    dark_diffs = []
    for y in range(2, h - 2, step_y):
        for x in range(2, w - 2, step_x):
            c = px[x, y]
            n = px[x, y - 1]
            s = px[x, y + 1]
            e = px[x + 1, y]
            we = px[x - 1, y]
            local_diff = abs(4 * c - n - s - e - we)
            if c > 140:
                bright_diffs.append(local_diff)
            elif c < 100:
                dark_diffs.append(local_diff)
    if not bright_diffs:
        return 0.0
    bright_var = sum(bright_diffs) / len(bright_diffs)
    return min(1.0, bright_var / 80.0)


def estimate_condition(image_bytes: bytes) -> dict[str, Any]:
    """Estimate coin condition from a photo.

    Returns:
        {
            "score": 0-100,
            "band": "Poor" | "Fine" | "Very Fine" | "Extremely Fine" |
                    "About Uncirculated" | "Mint State" | "Ungradeable",
            "signals": {luma, sharpness, wear_signal},
            "warnings": ["too dark", "blurry", "low contrast"],
            "confidence": 0-1,
            "caveat": "Photo-only screening, not a professional grade",
        }
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        return _error_result(f"Cannot open image: {e}")

    img = _resize_for_analysis(img, 512)
    gray = _to_grayscale(img)
    hist = _luma_histogram(gray)
    sharp = _sharpness(gray)
    wear = _wear_signal(gray)

    warnings: list[str] = []
    if hist["mean"] < 0.20:
        warnings.append("too dark")
    if hist["mean"] > 0.90:
        warnings.append("overexposed")
    if sharp < 0.2:
        warnings.append("blurry or out of focus")
    if hist["std"] < 0.15:
        warnings.append("low contrast")

    # Compose a score from the three signals.
    luma_score = max(0.0, min(1.0, (hist["mean"] - 0.1) / 0.7))
    sharp_score = max(0.0, min(1.0, sharp / 1.5))
    wear_score = wear  # already 0-1
    composite = (0.20 * luma_score) + (0.40 * sharp_score) + (0.40 * wear_score)

    # Translate to a verbal band. These are deliberately conservative — the
    # bands match common Sheldon-style coin grading terminology but we map them
    # to a tighter range to reflect the photo-only uncertainty.
    if warnings and composite < 0.25:
        band = "Ungradeable"
        score = int(composite * 100)
    elif composite < 0.30:
        band = "Poor / About Good"
        score = int(20 + composite * 80)
    elif composite < 0.50:
        band = "Fine / Very Fine"
        score = int(30 + composite * 100)
    elif composite < 0.65:
        band = "Extremely Fine"
        score = int(50 + composite * 60)
    elif composite < 0.80:
        band = "About Uncirculated"
        score = int(65 + composite * 40)
    else:
        band = "Mint State"
        score = int(min(95, 75 + composite * 25))

    # Confidence: lower when there are warnings OR signals disagree.
    confidence = 1.0 - 0.15 * len(warnings)
    if abs(sharp_score - wear_score) > 0.4:
        confidence -= 0.2  # signals disagree
    confidence = max(0.1, min(1.0, confidence))

    return {
        "score": score,
        "band": band,
        "signals": {
            "luma_mean": round(hist["mean"], 3),
            "luma_std": round(hist["std"], 3),
            "sharpness": round(sharp, 3),
            "wear_signal": round(wear, 3),
        },
        "warnings": warnings,
        "confidence": round(confidence, 2),
        "caveat": "Photo-only screening estimate. A real grade requires physical inspection.",
    }


def _error_result(msg: str) -> dict[str, Any]:
    return {
        "score": None,
        "band": "Ungradeable",
        "signals": {},
        "warnings": [msg],
        "confidence": 0.0,
        "caveat": "Condition could not be estimated.",
    }