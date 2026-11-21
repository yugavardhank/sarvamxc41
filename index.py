"""Vercel entry point. Keep the persistent Tokri backend on its own host."""
import os
from pathlib import Path

from dotenv import load_dotenv

from app.gateway import create_gateway_app

load_dotenv(Path(__file__).resolve().parent / ".env")
backend_url = os.getenv("API_URL", "").strip() or os.getenv("VITE_API_URL", "").strip()

if backend_url or os.getenv("VERCEL"):
    app = create_gateway_app(backend_url, deployed=bool(os.getenv("VERCEL")))
else:
    from app.main import app
