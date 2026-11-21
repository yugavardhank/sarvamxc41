"""Hits the real Sarvam API once per call type, to sanity-check credentials
and model names outside of the app. Requires MOCK_SARVAM=0 and a real
SARVAM_API_KEY in .env. Run with: python scripts/smoke_sarvam.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import sarvam


def main():
    print("extract_items:", sarvam.extract_items("toor dal and salt, nothing too expensive", "en-IN"))
    path = sarvam.speak("Your order total is 221 rupees.", "en-IN")
    print("speak wrote:", path)


if __name__ == "__main__":
    main()
