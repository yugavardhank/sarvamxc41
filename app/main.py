from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, MEDIA_DIR
from app.db import init_db, seed
from app.routes import cashfree, dev, relay

app = FastAPI(title="Tokri")

init_db()
seed()

# Relay (or any other external voice orchestrator) calls these endpoints
# cross-origin from its own hosted canvas, so CORS has to be open here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
app.mount(
    "/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static"
)

app.include_router(dev.router)
app.include_router(cashfree.router)
app.include_router(relay.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def dashboard():
    return FileResponse(str(BASE_DIR / "app" / "static" / "dashboard.html"))
