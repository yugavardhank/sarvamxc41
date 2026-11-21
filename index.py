"""Vercel entry point. Keep the persistent Tokri backend on its own host."""
import os
from pathlib import Path

from dotenv import load_dotenv

from app.gateway import create_gateway_app

load_dotenv(Path(__file__).resolve().parent / ".env")
backend_url = os.getenv("API_URL", "").strip() or os.getenv("VITE_API_URL", "").strip()

# This is always the deployment gateway, even when Vercel's automatic
# system variables are disabled or the backend URL is missing. Importing
# app.main here would initialize SQLite/audio files on a read-only host.
# Local backend development uses `uvicorn app.main:app` explicitly.
app = create_gateway_app(backend_url, deployed=True)
