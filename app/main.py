"""FastAPI app entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import (
    routes_chat,
    routes_dashboard,
    routes_detect,
    routes_export,
    routes_findings,
    routes_ingest,
    routes_ingestions,
    routes_released,
    routes_resources,
    routes_rules,
)
from .db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Cloud Cost Optimizer & Remediation Engine",
    description=(
        "Ingests AWS/Azure billing exports and resource inventory, detects orphaned/idle "
        "resources, and generates the CLI commands needed to decommission them. "
        "All remediation commands are generated only — never executed."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(routes_ingest.router)
app.include_router(routes_ingestions.router)
app.include_router(routes_detect.router)
app.include_router(routes_findings.router)
app.include_router(routes_released.router)
app.include_router(routes_resources.router)
app.include_router(routes_rules.router)
app.include_router(routes_export.router)
app.include_router(routes_chat.router)
app.include_router(routes_dashboard.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
