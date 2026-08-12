"""Configuration for coinscope backend. Loaded once at startup, then overlaid
by data/setup.json (first-run wizard). Environment variables always win.
"""
from __future__ import annotations
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PHOTOS_DIR = DATA_DIR / "photos"
CANONICAL_DIR = DATA_DIR / "canonical"
DB_PATH = DATA_DIR / "coinscope.db"

# qwen2.5vl:7b — best speed/accuracy for reading dates + mint marks on an RTX 4070 Ti.
# Fits in 12 GB easily (~6 GB weights). num_ctx is kept small in the client for latency.
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_RARITY_MODEL = "qwen2.5vl:7b"  # same GPU; skip extra pull when possible

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("COINSCOPE_MODEL", DEFAULT_VISION_MODEL)
RARITY_MODEL = os.environ.get("COINSCOPE_RARITY_MODEL", DEFAULT_RARITY_MODEL)

USE_CANONICAL_SHORTCUT = os.environ.get("COINSCOPE_CANONICAL", "1") == "1"

HOST = os.environ.get("COINSCOPE_HOST", "0.0.0.0")
PORT = int(os.environ.get("COINSCOPE_PORT", "8000"))

DEV_TOKEN = "coinscope-dev-token-change-me"
API_TOKEN = os.environ.get("COINSCOPE_TOKEN") or DEV_TOKEN

MAX_UPLOAD_MB = int(os.environ.get("COINSCOPE_MAX_UPLOAD_MB", "20"))
IDENTIFY_TIMEOUT_S = float(os.environ.get("COINSCOPE_IDENTIFY_TIMEOUT", "25"))
MODEL_MAX_DIM = int(os.environ.get("COINSCOPE_MODEL_MAX_DIM", "768"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
CANONICAL_DIR.mkdir(parents=True, exist_ok=True)
