import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import settings
from .db import SessionLocal, init_db
from .seed import generate_and_seed


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
                generate_and_seed(db)
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


@app.get("/")
def root():
    return {
        "service": "KSPDB Fault Localization",
        "docs": "/docs",
        "health": "/api/health",
    }
