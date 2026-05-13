"""Database-side ingestion. Creates an Ingestion row per uploaded file.

Each ingest function returns the Ingestion record so callers can show warnings
and trigger downstream detection.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..models import BillingRecord, Ingestion, ReleasedResource, Resource
from .aws import parse_aws_cur
from .azure import parse_azure_export
from .infer import infer_resources_from_billing
from .schema import NormalizedBilling, NormalizedResource


# ---------- Helpers ----------

def _status_from(rows_ingested: int, rows_total: int, warnings: list[str]) -> str:
    if rows_total == 0:
        return "failed" if not warnings else "partial"
    if rows_ingested == 0:
        return "failed"
    if rows_ingested < rows_total or warnings:
        return "partial"
    return "success"


def _check_reappearance(
    session: Session, ingestion: Ingestion, resource_ids: set[str]
) -> None:
    """Add warnings if any resource_ids have a prior ReleasedResource entry.

    A resource being marked released represents an operator commitment to deleting
    or deallocating it. If that same resource shows up in a fresh export, either
    the remediation didn't take effect or the resource came back — either way the
    operator should know.
    """
    if not resource_ids:
        return
    prior = (
        session.query(ReleasedResource)
        .filter(ReleasedResource.resource_id.in_(resource_ids))
        .all()
    )
    if not prior:
        return
    notices: list[str] = []
    by_rid: dict[str, list[ReleasedResource]] = {}
    for rel in prior:
        by_rid.setdefault(rel.resource_id, []).append(rel)
    for rid, rels in by_rid.items():
        detectors = ", ".join(sorted({r.detector for r in rels}))
        latest = max(r.released_at for r in rels if r.released_at).date().isoformat()
        notices.append(
            f"Resource '{rid}' was previously marked released ({detectors}) on {latest}, "
            "but reappeared in this ingest. Check whether the remediation command was run."
        )
    ingestion.warnings = (ingestion.warnings or []) + notices


def _coerce_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


# ---------- Billing ----------

def _persist_billing(
    session: Session, ingestion: Ingestion, rows: Iterable[NormalizedBilling]
) -> int:
    count = 0
    for r in rows:
        session.add(
            BillingRecord(
                provider=r.provider,
                resource_id=r.resource_id,
                service=r.service,
                usage_start=r.usage_start,
                usage_end=r.usage_end,
                cost=r.cost,
                currency=r.currency,
                region=r.region,
                usage_type=r.usage_type,
                account_id=r.account_id,
                raw=r.raw,
                ingestion_id=ingestion.id,
            )
        )
        count += 1
    session.flush()
    return count


def ingest_billing_file(
    session: Session,
    filename: str,
    content: bytes,
    size_bytes: Optional[int] = None,
    ingestion_id: Optional[int] = None,
) -> Ingestion:
    """
    Sniff billing format by extension/content, parse, persist, return the Ingestion row.
    Always commits the Ingestion row even on parse failure (status='failed') so the
    user can see what went wrong on the Ingestions page.

    If `ingestion_id` is given, fills that existing row instead of creating a new one
    (used by the streaming/background path).
    """
    if ingestion_id is not None:
        ingestion = session.get(Ingestion, ingestion_id)
        if ingestion is None:
            raise ValueError(f"ingestion {ingestion_id} not found")
    else:
        ingestion = Ingestion(
            filename=filename,
            kind="billing",
            size_bytes=size_bytes if size_bytes is not None else len(content),
            warnings=[],
        )
        session.add(ingestion)
        session.flush()  # get id

    lower = (filename or "").lower()
    detected_format = "unknown"
    detected_provider = "unknown"

    try:
        if lower.endswith(".json"):
            records, warnings, rows_total = parse_azure_export(content)
            detected_format = "azure_export_json"
            detected_provider = "azure"
        elif lower.endswith(".csv"):
            records, warnings, rows_total = parse_aws_cur(content)
            detected_format = "aws_cur_csv"
            detected_provider = "aws"
        else:
            # Best-effort content sniff
            head = content[:1].decode("utf-8", errors="ignore") if content else ""
            if head in ("[", "{"):
                records, warnings, rows_total = parse_azure_export(content)
                detected_format = "azure_export_json"
                detected_provider = "azure"
            else:
                records, warnings, rows_total = parse_aws_cur(content)
                detected_format = "aws_cur_csv"
                detected_provider = "aws"

        rows_ingested = _persist_billing(session, ingestion, records)
        ingestion.detected_format = detected_format
        ingestion.detected_provider = detected_provider
        ingestion.rows_total = rows_total
        ingestion.rows_ingested = rows_ingested
        ingestion.rows_skipped = max(rows_total - rows_ingested, 0)
        ingestion.warnings = warnings
        ingestion.status = _status_from(rows_ingested, rows_total, warnings)
        if rows_ingested > 0:
            infer_resources_from_billing(session, ingestion)
            touched = {r.resource_id for r in records if r.resource_id}
            _check_reappearance(session, ingestion, touched)
    except Exception as exc:
        ingestion.status = "failed"
        ingestion.error_message = str(exc)
        ingestion.warnings = (ingestion.warnings or []) + [
            "Parser raised: " + str(exc)
        ]
        ingestion.detected_format = detected_format
        ingestion.detected_provider = detected_provider

    session.flush()
    return ingestion


# ---------- Inventory ----------

REQUIRED_INV_FIELDS = ("resource_id", "provider", "resource_type")
KNOWN_PROVIDERS = {"aws", "azure"}


def _parse_inventory(
    content: bytes | str,
) -> tuple[list[NormalizedResource], list[str], int]:
    warnings: list[str] = []
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse JSON: {exc}") from exc

    if isinstance(payload, dict) and "resources" in payload:
        rows = payload["resources"]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("Inventory JSON must be a list or {resources: [...]}.")

    rows_total = len(rows)
    if rows_total == 0:
        warnings.append("File is empty (0 resources).")
        return [], warnings, 0

    records: list[NormalizedResource] = []
    missing_field_rows = 0
    unknown_provider_rows = 0

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            warnings.append(f"Row {idx}: not an object; skipped.")
            missing_field_rows += 1
            continue

        missing = [f for f in REQUIRED_INV_FIELDS if not row.get(f)]
        if missing:
            warnings.append(
                f"Row {idx} ({row.get('resource_id', '<no-id>')}): "
                f"missing required field(s): {', '.join(missing)}; skipped."
            )
            missing_field_rows += 1
            continue

        provider = str(row["provider"]).lower()
        if provider not in KNOWN_PROVIDERS:
            warnings.append(
                f"Row {idx} ({row['resource_id']}): unknown provider "
                f"'{row['provider']}' (expected one of: {', '.join(sorted(KNOWN_PROVIDERS))}); "
                "ingested as-is but detectors won't match."
            )
            unknown_provider_rows += 1

        records.append(
            NormalizedResource(
                resource_id=str(row["resource_id"]),
                provider=provider,
                resource_type=str(row["resource_type"]),
                region=str(row.get("region", "")),
                state=row.get("state"),
                attachments=row.get("attachments"),
                last_activity_at=_coerce_dt(row.get("last_activity_at")),
                cpu_avg_7d=row.get("cpu_avg_7d"),
                net_avg_7d=row.get("net_avg_7d"),
                request_avg_7d=row.get("request_avg_7d"),
                created_at=_coerce_dt(row.get("created_at")),
                tags=row.get("tags"),
                resource_group=row.get("resource_group"),
                extra=row.get("extra"),
                account_id=str(row["account_id"]) if row.get("account_id") else None,
                raw=row,
            )
        )

    return records, warnings, rows_total


def _upsert_inventory(
    session: Session, ingestion: Ingestion, records: list[NormalizedResource]
) -> int:
    n = 0
    for nr in records:
        existing = (
            session.query(Resource).filter(Resource.resource_id == nr.resource_id).first()
        )
        if existing is None:
            session.add(
                Resource(
                    resource_id=nr.resource_id,
                    provider=nr.provider,
                    resource_type=nr.resource_type,
                    region=nr.region,
                    state=nr.state,
                    attachments=nr.attachments,
                    last_activity_at=nr.last_activity_at,
                    cpu_avg_7d=nr.cpu_avg_7d,
                    net_avg_7d=nr.net_avg_7d,
                    request_avg_7d=nr.request_avg_7d,
                    created_at=nr.created_at,
                    tags=nr.tags,
                    resource_group=nr.resource_group,
                    extra=nr.extra,
                    raw=nr.raw,
                    ingestion_id=ingestion.id,
                    account_id=nr.account_id,
                    is_inferred=False,
                )
            )
        else:
            # Explicit inventory upload — promote any prior inferred row to authoritative
            # and overwrite stateful fields. Preserve first/last_seen_at (they come from billing).
            existing.provider = nr.provider
            existing.resource_type = nr.resource_type
            existing.region = nr.region
            existing.state = nr.state
            existing.attachments = nr.attachments
            existing.last_activity_at = nr.last_activity_at
            existing.cpu_avg_7d = nr.cpu_avg_7d
            existing.net_avg_7d = nr.net_avg_7d
            existing.request_avg_7d = nr.request_avg_7d
            existing.created_at = nr.created_at
            existing.tags = nr.tags
            existing.resource_group = nr.resource_group
            existing.extra = nr.extra
            existing.raw = nr.raw
            existing.ingestion_id = ingestion.id
            if nr.account_id:
                existing.account_id = nr.account_id
            existing.is_inferred = False
        n += 1
    session.flush()
    return n


def ingest_inventory_file(
    session: Session,
    filename: str,
    content: bytes,
    size_bytes: Optional[int] = None,
    ingestion_id: Optional[int] = None,
) -> Ingestion:
    if ingestion_id is not None:
        ingestion = session.get(Ingestion, ingestion_id)
        if ingestion is None:
            raise ValueError(f"ingestion {ingestion_id} not found")
        ingestion.detected_format = "inventory_json"
    else:
        ingestion = Ingestion(
            filename=filename,
            kind="inventory",
            detected_format="inventory_json",
            size_bytes=size_bytes if size_bytes is not None else len(content),
            warnings=[],
        )
        session.add(ingestion)
        session.flush()

    try:
        records, warnings, rows_total = _parse_inventory(content)
        providers = {r.provider for r in records}
        if len(providers) == 1:
            ingestion.detected_provider = providers.pop()
        elif len(providers) > 1:
            ingestion.detected_provider = "mixed"
        else:
            ingestion.detected_provider = "unknown"

        rows_ingested = _upsert_inventory(session, ingestion, records)
        ingestion.rows_total = rows_total
        ingestion.rows_ingested = rows_ingested
        ingestion.rows_skipped = max(rows_total - rows_ingested, 0)
        ingestion.warnings = warnings
        ingestion.status = _status_from(rows_ingested, rows_total, warnings)
        _check_reappearance(session, ingestion, {r.resource_id for r in records})
    except Exception as exc:
        ingestion.status = "failed"
        ingestion.error_message = str(exc)
        ingestion.warnings = (ingestion.warnings or []) + [
            "Parser raised: " + str(exc)
        ]

    session.flush()
    return ingestion


# ---------- Back-compat shims ----------
# Kept so the test suite from earlier turns still passes shape-wise; they
# now return row counts derived from the Ingestion record.

def ingest_aws_cur_csv(session: Session, content: bytes | str) -> int:
    if isinstance(content, str):
        content = content.encode("utf-8")
    ing = ingest_billing_file(session, "aws_cur.csv", content)
    return ing.rows_ingested


def ingest_azure_billing_json(session: Session, content: bytes | str) -> int:
    if isinstance(content, str):
        content = content.encode("utf-8")
    ing = ingest_billing_file(session, "azure_export.json", content)
    return ing.rows_ingested


def ingest_inventory_json(session: Session, content: bytes | str) -> int:
    if isinstance(content, str):
        content = content.encode("utf-8")
    ing = ingest_inventory_file(session, "inventory.json", content)
    return ing.rows_ingested


def sniff_and_ingest_billing(
    session: Session, filename: str, content: bytes
) -> tuple[str, int]:
    ing = ingest_billing_file(session, filename, content)
    return (ing.detected_format or "unknown", ing.rows_ingested)
