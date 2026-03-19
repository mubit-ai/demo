"""Configuration — reads from environment variables (with .env fallback)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
MUBIT_ENDPOINT = os.environ.get("MUBIT_ENDPOINT", "http://127.0.0.1:3000")
MUBIT_API_KEY = os.environ.get("MUBIT_API_KEY", "")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
CRASH_AFTER_PHASE = int(os.environ.get("CRASH_AFTER_PHASE", "3"))
