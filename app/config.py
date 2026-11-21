import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() == "1"


SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
# A *separate* key from SARVAM_API_KEY above: this one is scoped to the
# Voice Agents (Samvaad) platform and is what the Instant Outbound API's
# X-API-Key header needs. Generated from Deploy with code -> Generate API key.
SARVAM_VOICE_AGENTS_API_KEY = os.getenv("SARVAM_VOICE_AGENTS_API_KEY", "")
# Needed only to trigger a REAL outbound phone call via Sarvam's Instant
# Outbound API (app/services/sarvam.py:trigger_outbound_call). Get these
# from the Sarvam dashboard after building+deploying an agent and setting
# up a phone connection (Deploy -> Phone Numbers).
SARVAM_ORG_ID = os.getenv("SARVAM_ORG_ID", "")
SARVAM_WORKSPACE_ID = os.getenv("SARVAM_WORKSPACE_ID", "")
SARVAM_APP_ID = os.getenv("SARVAM_APP_ID", "")
SARVAM_APP_VERSION = int(os.getenv("SARVAM_APP_VERSION", "1"))
SARVAM_CONNECTION_ID = os.getenv("SARVAM_CONNECTION_ID", "")
SARVAM_AGENT_PHONE_NUMBER = os.getenv("SARVAM_AGENT_PHONE_NUMBER", "")
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
