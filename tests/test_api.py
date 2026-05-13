"""End-to-end API tests using FastAPI TestClient."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as db_module
from app.db import Base
from app.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _wait_ingestion(client, ingestion_id: int, max_tries: int = 30) -> dict:
    """Poll /api/ingestions/{id} until processing_state=done. Returns the final dict."""
    import time
    for _ in range(max_tries):
        r = client.get(f"/api/ingestions/{ingestion_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("processing_state") == "done":
            return body
        time.sleep(0.05)
    raise AssertionError(f"ingestion {ingestion_id} did not finish in time")


def _upload_and_wait(client, url: str, sample_name: str, mime: str = "application/json") -> dict:
    """POST a sample file, then wait for background processing to complete."""
    with (SAMPLES / sample_name).open("rb") as f:
        r = client.post(url, files={"file": (sample_name, f, mime)})
    assert r.status_code == 200, r.text
    return _wait_ingestion(client, r.json()["id"])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test.sqlite"
    engine = create_engine(
        f"sqlite:///{test_db}", future=True, connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_full_pipeline(client):
    body = _upload_and_wait(client, "/api/ingest/billing", "aws_cur_sample.csv", "text/csv")
    assert body["rows_ingested"] == 10
    assert body["status"] == "success"
    assert body["processing_state"] == "done"

    body = _upload_and_wait(client, "/api/ingest/billing", "azure_export_sample.json")
    assert body["rows_ingested"] == 4

    for inv in ("aws_inventory_sample.json", "azure_inventory_sample.json"):
        _upload_and_wait(client, "/api/ingest/inventory", inv)

    # After inventory uploads, detection should have run and produced findings.
    r = client.get("/api/findings")
    assert r.status_code == 200
    findings = r.json()["findings"]
    assert len(findings) >= 7

    # Summary contains both providers
    r = client.get("/api/summary")
    s = r.json()
    assert s["findings_count"] >= 7
    assert s["total_monthly_waste"] > 0
    assert set(s["by_provider"].keys()) == {"aws", "azure"}

    # Ingestions list
    r = client.get("/api/ingestions")
    assert r.status_code == 200
    ings = r.json()["ingestions"]
    assert len(ings) == 4

    # Ingestion detail
    ing_id = ings[0]["id"]
    r = client.get(f"/api/ingestions/{ing_id}")
    assert r.status_code == 200
    detail = r.json()
    assert "detection_runs" in detail
    assert "sample" in detail

    # Release a finding
    target = findings[0]
    target_finding_id = target["id"]
    target_resource_id = target["resource"]["id"]
    target_detector = target["detector"]
    r = client.post(f"/api/findings/{target_finding_id}/release", json={"note": "tested"})
    assert r.status_code == 200, r.text
    released = r.json()
    assert released["monthly_cost_saved"] > 0
    assert released["note"] == "tested"

    # Released list
    r = client.get("/api/released")
    body = r.json()
    assert body["count"] == 1
    assert body["total_monthly_saved"] > 0

    # The released (resource_id, detector) pair must not reappear in the next run.
    r = client.post("/api/detect/run")
    assert r.status_code == 200
    r = client.get("/api/findings")
    open_pairs = {(f["resource"]["id"], f["detector"]) for f in r.json()["findings"]}
    assert (target_resource_id, target_detector) not in open_pairs

    # Dashboard pages render
    for path in ("/", "/ingestions", f"/ingestions/{ing_id}", "/released", "/rules", "/inventory", "/inventory?show_released=true"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert "Cloud Cost Optimizer" in r.text

    # Resource detail page renders for a known resource
    r = client.get("/resources/vol-0a1b2c3d4e5f60001")
    assert r.status_code == 200
    assert "Cloud Cost Optimizer" in r.text

    # Ingestion sample for an inventory file includes matched findings per row
    inv_id = next(i["id"] for i in ings if i["kind"] == "inventory")
    r = client.get(f"/api/ingestions/{inv_id}")
    body = r.json()
    assert body["kind"] == "inventory"
    flagged = [s for s in body["sample"] if s.get("findings")]
    assert flagged, "expected at least one resource in this file to have a matched rule"
    # the matched-rule shape is {id, detector, severity, monthly_cost_estimate, reason, remediation_command}
    sample_finding = flagged[0]["findings"][0]
    assert "detector" in sample_finding and "remediation_command" in sample_finding


def test_rules_endpoint(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    rules = r.json()["rules"]
    slugs = {r["slug"] for r in rules}
    assert slugs == {
        "orphan_ebs_volume",
        "orphan_azure_disk",
        "idle_ec2",
        "idle_azure_vm",
        "old_ebs_snapshot",
        "idle_elb",
        "unassociated_eip",
        "idle_eip_by_billing",
        "unmonitored_long_running",
    }

    # Each rule shape
    for rule in rules:
        assert rule["title"]
        assert rule["description"]
        assert rule["providers"]
        assert rule["resource_types"]
        assert rule["severity"] in {"low", "medium", "high"}
        assert rule["criteria"]
        assert rule["remediation_action"]
        assert isinstance(rule["thresholds"], list)
        assert isinstance(rule["requires"], list) and rule["requires"]
        for src in rule["requires"]:
            assert src in {"billing", "inventory"}

    # Inventory-required vs billing-only split is correct.
    eip_billing = next(r for r in rules if r["slug"] == "idle_eip_by_billing")
    assert eip_billing["requires"] == ["billing"]
    orphan_ebs = next(r for r in rules if r["slug"] == "orphan_ebs_volume")
    assert orphan_ebs["requires"] == ["inventory"]

    # idle_ec2 has two thresholds with current values surfaced
    ec2 = next(r for r in rules if r["slug"] == "idle_ec2")
    threshold_names = {t["name"] for t in ec2["thresholds"]}
    assert threshold_names == {"cpu_pct", "net_bytes_per_day"}
    for t in ec2["thresholds"]:
        assert "current_value" in t
        assert t["env_var"]

    # Single-rule endpoint
    r = client.get("/api/rules/orphan_ebs_volume")
    assert r.status_code == 200
    assert r.json()["slug"] == "orphan_ebs_volume"

    r = client.get("/api/rules/does_not_exist")
    assert r.status_code == 404


def test_inventory_listing(client):
    """Full inventory roundtrip — listing aggregates findings + cost + release state."""
    for path in ("aws_cur_history_sample.csv", "aws_cur_sample.csv"):
        _upload_and_wait(client, "/api/ingest/billing", path, "text/csv")
    for path in ("aws_inventory_sample.json", "azure_inventory_sample.json"):
        _upload_and_wait(client, "/api/ingest/inventory", path)

    # Default view: include_released=false, all resources active
    r = client.get("/api/resources")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    assert body["include_released"] is False

    # Find an open finding and release it
    f_list = client.get("/api/findings").json()["findings"]
    target = next(f for f in f_list if f["detector"] == "orphan_ebs_volume")
    client.post(f"/api/findings/{target['id']}/release", json={"note": "inventory test"})

    # Now released resource should be HIDDEN by default
    r = client.get("/api/resources")
    rids_default = {r["resource_id"] for r in r.json()["resources"]}
    assert target["resource"]["id"] not in rids_default

    # ... and VISIBLE with include_released=true
    r = client.get("/api/resources?include_released=true")
    rids_all = {r["resource_id"] for r in r.json()["resources"]}
    assert target["resource"]["id"] in rids_all
    released_entry = next(
        r for r in r.json()["resources"] if r["resource_id"] == target["resource"]["id"]
    )
    assert released_entry["released_count"] >= 1
    assert "orphan_ebs_volume" in released_entry["released_detectors"]


def test_inventory_filtering_and_sorting(client):
    """Inventory listing supports provider/type/status/search filters and configurable sort."""
    for path in ("aws_cur_history_sample.csv", "aws_cur_sample.csv"):
        _upload_and_wait(client, "/api/ingest/billing", path, "text/csv")
    for path in ("aws_inventory_sample.json", "azure_inventory_sample.json"):
        _upload_and_wait(client, "/api/ingest/inventory", path)

    # Provider filter
    r = client.get("/api/resources?provider=azure")
    providers = {x["provider"] for x in r.json()["resources"]}
    assert providers == {"azure"}

    # Resource-type filter
    r = client.get("/api/resources?resource_type=EBS_VOLUME")
    types = {x["resource_type"] for x in r.json()["resources"]}
    assert types == {"EBS_VOLUME"}

    # Search (substring on resource_id, case-insensitive)
    r = client.get("/api/resources?search=BILLED-ONLY")
    rids = {x["resource_id"] for x in r.json()["resources"]}
    assert any("billed-only" in rid for rid in rids)

    # Status=open should exclude released and clean
    f_list = client.get("/api/findings").json()["findings"]
    target = next(f for f in f_list if f["detector"] == "orphan_ebs_volume")
    client.post(f"/api/findings/{target['id']}/release", json={"note": "test"})

    r = client.get("/api/resources?status=open")
    body = r.json()
    assert body["filters"]["status"] == "open"
    for x in body["resources"]:
        assert x["open_findings_count"] > 0
        assert x["released_count"] == 0

    # Status=released only shows released (overrides default-hide)
    r = client.get("/api/resources?status=released")
    body = r.json()
    assert len(body["resources"]) >= 1
    for x in body["resources"]:
        assert x["released_count"] >= 1

    # Sort by resource_id ascending
    r = client.get("/api/resources?sort=resource_id&order=asc")
    ids = [x["resource_id"] for x in r.json()["resources"]]
    assert ids == sorted(ids, key=str.lower)

    # Sort by total_cost ascending
    r = client.get("/api/resources?sort=total_cost&order=asc")
    costs = [x["total_cost"] for x in r.json()["resources"]]
    assert costs == sorted(costs)

    # Facets list providers + resource types found in DB
    r = client.get("/api/resources")
    facets = r.json()["facets"]
    assert "aws" in facets["providers"] and "azure" in facets["providers"]
    assert "EBS_VOLUME" in facets["resource_types"]
    assert "total_cost" in facets["sorts"]
    # provider_types is the cross-filter map for the UI: only supported providers.
    assert set(facets["provider_types"].keys()).issubset({"aws", "azure"})
    assert "EBS_VOLUME" in facets["provider_types"].get("aws", [])
    assert all("AZURE_" not in t for t in facets["provider_types"].get("aws", []))
    assert all(t.startswith("AZURE_") for t in facets["provider_types"].get("azure", []))


def test_facets_exclude_unsupported_providers(client):
    """If an ingestion contains a gcp resource, it must not appear as a filter option."""
    _upload_and_wait(client, "/api/ingest/inventory", "inventory_with_warnings_sample.json")

    r = client.get("/api/resources")
    facets = r.json()["facets"]
    # Confirm a gcp row exists in the DB but isn't surfaced as a filter option.
    r_all = client.get("/api/resources?include_released=true").json()["resources"]
    assert any(x["provider"] == "gcp" for x in r_all), (
        "expected a gcp row in raw listing"
    )
    assert "gcp" not in facets["providers"]
    assert "gcp" not in facets["provider_types"]
    # GCE_INSTANCE was the gcp resource type — must not leak into resource_types facet
    # (since it's filtered alongside its unsupported provider).
    assert "GCE_INSTANCE" not in facets["resource_types"]
    # supported_providers is exposed verbatim for the UI to lean on.
    assert facets["supported_providers"] == ["aws", "azure"]


def test_reappearance_warning_on_reingest(client):
    """If a released resource reappears in a subsequent ingest, warn the operator."""
    _upload_and_wait(client, "/api/ingest/billing", "aws_cur_sample.csv", "text/csv")
    _upload_and_wait(client, "/api/ingest/inventory", "aws_inventory_sample.json")

    findings = client.get("/api/findings").json()["findings"]
    target = next(f for f in findings if f["detector"] == "orphan_ebs_volume")
    rid = target["resource"]["id"]
    client.post(f"/api/findings/{target['id']}/release", json={"note": "verified"})

    body = _upload_and_wait(client, "/api/ingest/inventory", "aws_inventory_sample.json")
    assert body["status"] in ("success", "partial")
    reappearance_warnings = [w for w in body["warnings"] if rid in w and "previously marked released" in w]
    assert reappearance_warnings, f"expected a reappearance warning for {rid}; got {body['warnings']}"


def test_bulk_release(client):
    _upload_and_wait(client, "/api/ingest/billing", "aws_cur_sample.csv", "text/csv")
    _upload_and_wait(client, "/api/ingest/inventory", "aws_inventory_sample.json")
    findings = client.get("/api/findings").json()["findings"]
    ids = [f["id"] for f in findings[:3]]
    r = client.post("/api/findings/bulk-release", json={"finding_ids": ids, "note": "batch"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released_count"] == 3
    assert body["monthly_cost_saved"] > 0
    # All released entries now visible
    rel = client.get("/api/released").json()
    assert rel["count"] == 3


def test_bulk_release_rejects_empty_list(client):
    r = client.post("/api/findings/bulk-release", json={"finding_ids": []})
    assert r.status_code == 400


def test_csv_exports(client):
    _upload_and_wait(client, "/api/ingest/billing", "aws_cur_sample.csv", "text/csv")
    _upload_and_wait(client, "/api/ingest/inventory", "aws_inventory_sample.json")

    r = client.get("/api/export/findings.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "resource_id" in text and "remediation_command" in text
    # at least header + 1 row
    assert text.count("\r\n") >= 1 or text.count("\n") >= 2

    r = client.get("/api/export/resources.csv?include_released=true")
    assert r.status_code == 200
    assert "resource_id" in r.text and "first_seen_at" in r.text and "account_id" in r.text

    # Release something so the released CSV has content
    f = client.get("/api/findings").json()["findings"][0]
    client.post(f"/api/findings/{f['id']}/release", json={"note": "csv test"})
    r = client.get("/api/export/released.csv")
    assert r.status_code == 200
    assert "released_at" in r.text and "csv test" in r.text


def test_resource_detail_and_history(client):
    """End-to-end: ingest, then resource endpoints expose time-history + provenance."""
    _upload_and_wait(client, "/api/ingest/billing", "aws_cur_history_sample.csv", "text/csv")
    _upload_and_wait(client, "/api/ingest/billing", "aws_cur_sample.csv", "text/csv")

    # Resource detail
    r = client.get("/api/resources/i-billed-only-deadbeef00")
    assert r.status_code == 200
    body = r.json()
    assert body["is_inferred"] is True
    assert body["resource_type"] == "EC2_INSTANCE"
    assert body["first_seen_at"]
    assert body["last_seen_at"]
    assert body["total_cost"] > 0
    assert body["billing_rows_count"] >= 3

    # Billing history time series
    r = client.get("/api/resources/i-billed-only-deadbeef00/billing-history")
    assert r.status_code == 200
    hist = r.json()
    assert hist["resource_id"] == "i-billed-only-deadbeef00"
    assert len(hist["points"]) >= 3
    assert hist["total_cost"] > 0
    assert "BoxUsage:t3.large" in hist["by_usage_type"]

    # Unknown resource → 404
    r = client.get("/api/resources/does-not-exist-anywhere")
    assert r.status_code == 404


def test_invalid_inventory_returns_warnings(client):
    body = _upload_and_wait(client, "/api/ingest/inventory", "inventory_with_warnings_sample.json")
    assert body["status"] == "partial"
    assert body["rows_skipped"] >= 1
    assert any("missing" in w for w in body["warnings"])


def test_broken_billing_records_failed_ingestion(client):
    r = client.post(
        "/api/ingest/billing",
        files={"file": ("garbage.json", b"this is not json or csv {", "application/json")},
    )
    assert r.status_code == 200
    body = _wait_ingestion(client, r.json()["id"])
    assert body["status"] == "failed"
    assert body["error_message"]
