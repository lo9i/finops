"""Chat endpoint tests. Live Claude calls aren't exercised here; we test the
graceful-degradation path and tool wiring at the function level."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_module
from app.db import Base
from app.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "chat_test.sqlite"
    engine = create_engine(
        f"sqlite:///{test_db}", future=True, connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    with TestClient(app) as c:
        yield c


def test_chat_status_without_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.get("/api/chat/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_chat_status_with_key(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    r = client.get("/api/chat/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": True}


def test_chat_endpoint_503_without_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 503
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_chat_endpoint_validates_input(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake")
    # Empty message → validation error
    r = client.post("/api/chat", json={"message": ""})
    assert r.status_code == 422


def test_chat_tools_query_live_db(client, monkeypatch):
    """The tool functions themselves are testable without hitting the API.

    Verify each tool returns valid JSON pointing at the test DB state.
    """
    # Ingest a sample so the DB has something to find
    with (SAMPLES / "aws_cur_sample.csv").open("rb") as f:
        client.post("/api/ingest/billing", files={"file": ("aws_cur_sample.csv", f, "text/csv")})
    with (SAMPLES / "aws_inventory_sample.json").open("rb") as f:
        client.post("/api/ingest/inventory", files={"file": ("aws_inventory_sample.json", f, "application/json")})

    # Wait for processing
    import time
    for _ in range(30):
        ings = client.get("/api/ingestions").json()["ingestions"]
        if ings and all(i["processing_state"] == "done" for i in ings):
            break
        time.sleep(0.05)

    import json
    from app.ai.tools import list_resources, list_findings, get_summary, list_rules, get_resource

    # The @beta_tool decorator wraps the function in a BetaFunctionTool that
    # keeps the original callable on `.func`.
    payload = json.loads(list_resources.func(provider="aws", limit=5))
    assert payload["count"] >= 1
    assert all(r["provider"] == "aws" for r in payload["resources"])

    payload = json.loads(list_findings.func(detector="orphan_ebs_volume"))
    assert payload["total"] >= 1

    payload = json.loads(get_summary.func())
    assert "total_monthly_waste" in payload
    assert "by_account" in payload

    payload = json.loads(list_rules.func())
    slugs = {r["slug"] for r in payload}
    assert "idle_ec2" in slugs
    assert "unmonitored_long_running" in slugs

    payload = json.loads(get_resource.func(resource_id="vol-0a1b2c3d4e5f60001"))
    assert payload["resource_id"] == "vol-0a1b2c3d4e5f60001"
    # error path
    payload = json.loads(get_resource.func(resource_id="does-not-exist"))
    assert "error" in payload
