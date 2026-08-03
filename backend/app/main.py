import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .db import SessionLocal, init_db
from .seed import generate_and_seed


STATIC_DIR = Path(os.environ.get("STATIC_DIR", "")).resolve() if os.environ.get("STATIC_DIR") else None


def _ensure_sqlite_dir() -> None:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = url.replace("sqlite:///", "", 1)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _ensure_sqlite_dir()
    init_db()
    if settings.seed_on_startup:
        db = SessionLocal()
        try:
            from .db import Pole

            count = db.query(Pole).count()
            if count == 0:
                poles_target = int(os.environ.get("SEED_POLES", "3200"))
                generate_and_seed(db, poles_target=poles_target)
        finally:
            db.close()
    yield


app = FastAPI(title="KSPDB Fault Localization", version="1.0.0", lifespan=lifespan)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/api")
def api_root():
    return {
        "service": "KSPDB Fault Localization",
        "docs": "/docs",
        "health": "/api/health",
    }


if STATIC_DIR and STATIC_DIR.exists():
    assets = STATIC_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def spa_root():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Keep API/docs out of SPA fallback (router already owns /api/*)
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
else:

    @app.get("/")
    def root():
        return {
            "service": "KSPDB Fault Localization",
            "docs": "/docs",
            "health": "/api/health",
        }
