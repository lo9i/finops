"""Ingestion parser tests."""
from __future__ import annotations

import json
from pathlib import Path

from app.ingest import (
    ingest_billing_file,
    ingest_inventory_file,
    sniff_and_ingest_billing,
)
from app.models import BillingRecord, Ingestion, Resource

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_aws_cur_csv_parses_all_rows(session):
    ing = ingest_billing_file(
        session, "aws_cur_sample.csv", (SAMPLES / "aws_cur_sample.csv").read_bytes()
    )
    assert ing.status == "success"
    assert ing.rows_total == 10
    assert ing.rows_ingested == 10
    assert ing.detected_provider == "aws"
    assert ing.detected_format == "aws_cur_csv"
    assert (ing.warnings or []) == []
    rows = session.query(BillingRecord).filter_by(provider="aws").all()
    assert len(rows) == 10
    total = sum(r.cost for r in rows)
    assert total > 200


def test_azure_export_json_parses(session):
    ing = ingest_billing_file(
        session,
        "azure_export_sample.json",
        (SAMPLES / "azure_export_sample.json").read_bytes(),
    )
    assert ing.status == "success"
    assert ing.rows_ingested == 4
    assert ing.detected_provider == "azure"
    ids = {r.resource_id for r in session.query(BillingRecord).all()}
    assert "orphan-disk-1" in ids and "vm-idle-1" in ids


def test_inventory_upserts(session):
    payload = {
        "resources": [
            {
                "resource_id": "vol-x",
                "provider": "aws",
                "resource_type": "EBS_VOLUME",
                "region": "us-east-1",
                "state": "available",
            }
        ]
    }
    ing1 = ingest_inventory_file(session, "a.json", json.dumps(payload).encode())
    assert ing1.rows_ingested == 1
    payload["resources"][0]["state"] = "in-use"
    ing2 = ingest_inventory_file(session, "b.json", json.dumps(payload).encode())
    assert ing2.rows_ingested == 1
    rows = session.query(Resource).all()
    assert len(rows) == 1
    assert rows[0].state == "in-use"
    # Two ingestion rows were recorded
    assert session.query(Ingestion).count() == 2


def test_invalid_inventory_rows_produce_warnings(session):
    ing = ingest_inventory_file(
        session,
        "inventory_with_warnings_sample.json",
        (SAMPLES / "inventory_with_warnings_sample.json").read_bytes(),
    )
    assert ing.status == "partial"
    assert ing.rows_total == 4
    # 1 row missing resource_id → skipped; 1 unknown provider → ingested with warning
    assert ing.rows_ingested == 3
    assert ing.rows_skipped == 1
    assert any("missing required field" in w for w in ing.warnings)
    assert any("unknown provider" in w for w in ing.warnings)


def test_billing_with_unparseable_json_records_failure(session):
    ing = ingest_billing_file(session, "broken.json", b"not really json {")
    assert ing.status == "failed"
    assert ing.rows_ingested == 0
    assert ing.error_message
    assert ing.warnings  # has at least the parser-raised warning


def test_sniff_routes_by_extension(session):
    fmt, n = sniff_and_ingest_billing(
        session, "billing.csv", (SAMPLES / "aws_cur_sample.csv").read_bytes()
    )
    assert fmt == "aws_cur_csv"
    assert n == 10

    fmt2, n2 = sniff_and_ingest_billing(
        session, "azure.json", (SAMPLES / "azure_export_sample.json").read_bytes()
    )
    assert fmt2 == "azure_export_json"
    assert n2 == 4
