"""FastAPI application entry point.

Start it with: uvicorn app.main:app --host 127.0.0.1 --port 8000

What happens here, in order:
1. Load the .env file that sits next to this repository (API keys, paths, tuning).
2. Create the FastAPI app and allow the dashboard's browser origin through CORS
   (FRONTEND_ORIGINS in .env, comma separated).
3. On startup: mark runs that were interrupted by a previous shutdown
   (reconcile_incomplete_runs) and start the background worker threads that
   actually execute analyses (start_workers).
4. Expose two tiny always-on endpoints, "/" and "/health", then mount every
   real endpoint from app/routes/.

Nothing in this file talks to the AI engine directly; see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.mcp_server import mcp, mcp_http_app
from app.routes import router


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.analyze_service import reconcile_incomplete_runs
    from app.worker import start_workers

    reconcile_incomplete_runs()
    start_workers()
    if os.getenv("MCP_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        yield
        return
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Trading Analysis API",
    version="1.1.0",
    lifespan=lifespan,
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

@asynccontextmanager
async def lifespan(_: FastAPI):
    from app.services.analyze_service import reconcile_incomplete_runs, list_runs
    from app.artifacts import write_result_html_artifact
    from app.worker import start_workers

    reconcile_incomplete_runs()
    start_workers()

    # ★ NEW — regenerate result.html for completed runs using the new template
    try:
        for run in list_runs():
            if run.get("status") == "completed" and run.get("result"):
                try:
                    write_result_html_artifact(run["run_id"])
                except Exception:
                    pass
    except Exception:
        pass

    if os.getenv("MCP_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        yield
        return
    async with mcp.session_manager.run():
        yield




app.include_router(router)

if os.getenv("MCP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}:
    app.mount("/mcp", mcp_http_app)