import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() == "1"


SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
CASHFREE_APP_ID = os.getenv("CASHFREE_APP_ID", "")
CASHFREE_SECRET = os.getenv("CASHFREE_SECRET", "")
CASHFREE_ENV = os.getenv("CASHFREE_ENV", "sandbox")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "tokri.db"))
# Shared secret the voice-agent tool config must send back on every call to
# app/routes/relay.py, since that router is reachable from the public
# internet once PUBLIC_BASE_URL is live. Empty means no check (local dev only).
TOOL_SHARED_SECRET = os.getenv("TOOL_SHARED_SECRET", "")

MOCK_SARVAM = _flag("MOCK_SARVAM", "1")
MOCK_CASHFREE = _flag("MOCK_CASHFREE", "1")

MEDIA_DIR = BASE_DIR / "app" / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def media_url(path: str) -> str:
    """Converts a local file path under MEDIA_DIR into the URL the app serves it at."""
    return f"/media/{Path(path).name}"
