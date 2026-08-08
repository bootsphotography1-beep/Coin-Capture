"""Configuration for coinscope backend. Loaded once at startup."""
from __future__ import annotations
import os
from pathlib import Path

# Project root: ../ (coinscope/) from this file's location
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PHOTOS_DIR = DATA_DIR / "photos"
CANONICAL_DIR = DATA_DIR / "canonical"
DB_PATH = DATA_DIR / "coinscope.db"

# Ollama (local vision model)
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("COINSCOPE_MODEL", "moondream")

# If true, identified canonical coins short-circuit the LLM call for rarity data.
USE_CANONICAL_SHORTCUT = os.environ.get("COINSCOPE_CANONICAL", "1") == "1"

# Network
HOST = os.environ.get("COINSCOPE_HOST", "0.0.0.0")
PORT = int(os.environ.get("COINSCOPE_PORT", "8000"))

# Auth: shared token. The scanner page must send this in X-Coinscope-Token.
# Set COINSCOPE_TOKEN in your environment before exposing on the network.
DEV_TOKEN = "coinscope-dev-token-change-me"
API_TOKEN = os.environ.get("COINSCOPE_TOKEN") or DEV_TOKEN

# Upload limits
MAX_UPLOAD_MB = int(os.environ.get("COINSCOPE_MAX_UPLOAD_MB", "20"))

# Ensure dirs exist at import time
DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
CANONICAL_DIR.mkdir(parents=True, exist_ok=True)