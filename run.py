#!/usr/bin/env python3
"""Launcher for the coinscope backend.

Starts uvicorn with sensible defaults. Honors environment variables for
host, port, and token. See backend/config.py for all options.

Usage:
    python run.py                    # uses defaults
    COINSCOPE_PORT=9000 python run.py
    COINSCOPE_TOKEN=mySecret python run.py
"""
import os
import sys
from pathlib import Path

# Allow running from anywhere by putting backend/ on sys.path.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "backend"))

import config  # noqa: E402
import uvicorn  # noqa: E402

if __name__ == "__main__":
    # Run uvicorn programmatically so we don't need an extra 'uvicorn' CLI.
    uvicorn.run(
        "app:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        reload=False,
        ws_ping_interval=20,
        ws_ping_timeout=30,
    )