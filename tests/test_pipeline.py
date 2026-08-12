"""Unit tests that do not need Ollama or a GPU."""
from __future__ import annotations
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.canonical import lookup_canonical, rarity_flag
from backend.crop import crop_coin, downscale_for_model


def test_rarity_flags():
    assert rarity_flag("key") == "rare"
    assert rarity_flag("rare") == "rare"
    assert rarity_flag("semi-key") == "notable"
    assert rarity_flag("common") == "common"
    assert rarity_flag(None) == "unknown"


def test_canonical_wheat_1909():
    report = lookup_canonical({"series": "Lincoln Wheat Cent", "year": "1909"})
    assert report is not None
    assert report["rarity_tier"] == "semi-key"
    assert rarity_flag(report["rarity_tier"]) == "notable"


def test_canonical_shield_cent_from_denomination():
    report = lookup_canonical({"denomination": "One Cent", "year": "2019"})
    assert report is not None
    assert "Shield" in report["name"] or report["matched_series"] == "Lincoln Shield Cent"


def test_canonical_washington_quarter():
    report = lookup_canonical({"series": "Washington Quarter", "year": "1999"})
    assert report is not None
    assert report["rarity_tier"] == "common"


def _coin_like_jpeg() -> bytes:
    img = Image.new("RGB", (800, 800), (20, 22, 28))
    # gold disc in the center
    pix = img.load()
    cx, cy, r = 400, 400, 180
    for y in range(800):
        for x in range(800):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                pix[x, y] = (212, 165, 90)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_crop_returns_jpeg():
    out, meta = crop_coin(_coin_like_jpeg(), size=320)
    assert out[:2] == b"\xff\xd8"
    assert meta["output_size"] == [320, 320]
    img = Image.open(io.BytesIO(out))
    assert img.size == (320, 320)


def test_downscale_for_model():
    small = downscale_for_model(_coin_like_jpeg(), max_dim=128)
    img = Image.open(io.BytesIO(small))
    assert max(img.size) <= 128
