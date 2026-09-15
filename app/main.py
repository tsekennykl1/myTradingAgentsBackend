from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

app = FastAPI(
    title="Trading Analysis API",
    version="1.0.0",
)

frontend_origins = [value.strip() for value in os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080"
).split(",") if value.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event() -> None:
    from app.services.analyze_service import reconcile_incomplete_runs
    from app.worker import start_workers

    reconcile_incomplete_runs()
    start_workers()


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Trading Analysis API is running"
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "trading-analysis-api"
    }


app.include_router(router)