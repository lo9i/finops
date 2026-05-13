"""Reset command: drops + recreates schema, leaves the DB empty."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_module
from app import reset as reset_module
from app.db import Base
from app.ingest import ingest_billing_file
from app.models import BillingRecord, Ingestion, Resource

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_reset_clears_all_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "reset_test.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(reset_module, "engine", engine)
    monkeypatch.setattr(reset_module, "SessionLocal", SessionLocal)

    Base.metadata.create_all(engine)

    # Populate
    with SessionLocal() as s:
        ingest_billing_file(
            s, "aws_cur_sample.csv", (SAMPLES / "aws_cur_sample.csv").read_bytes()
        )
        s.commit()
        assert s.query(BillingRecord).count() == 10
        assert s.query(Ingestion).count() == 1
        assert s.query(Resource).count() >= 1  # inferred from billing

    # Reset without prompting
    reset_module.reset(confirm=False)

    with SessionLocal() as s:
        assert s.query(BillingRecord).count() == 0
        assert s.query(Ingestion).count() == 0
        assert s.query(Resource).count() == 0


def test_reset_works_on_empty_db(tmp_path, monkeypatch):
    """Calling reset on a fresh empty DB must not error."""
    db_path = tmp_path / "empty.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(reset_module, "engine", engine)
    monkeypatch.setattr(reset_module, "SessionLocal", SessionLocal)

    # No create_all — let reset itself create the schema.
    reset_module.reset(confirm=False)

    # After reset, schema exists and tables are empty.
    with SessionLocal() as s:
        assert s.query(Ingestion).count() == 0
