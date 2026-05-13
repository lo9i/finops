"""CSV exports for findings, resources, and released entries."""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Finding, ReleasedResource, Resource
from .routes_findings import query_findings
from .routes_resources import list_resources

router = APIRouter(prefix="/api/export", tags=["export"])


def _stream_csv(rows: list[dict], columns: list[str], filename: str) -> StreamingResponse:
    """Encode `rows` as CSV and return a download response."""

    def gen():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for row in rows:
            writer.writerow(row)
            data = buf.getvalue()
            buf.seek(0); buf.truncate(0)
            yield data

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/findings.csv")
def export_findings(
    provider: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    detector: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    findings = query_findings(session, provider=provider, severity=severity, detector=detector)
    rows = []
    for f in findings:
        rows.append({
            "id": f["id"],
            "severity": f["severity"],
            "detector": f["detector"],
            "resource_id": f["resource"]["id"],
            "provider": f["resource"]["provider"],
            "resource_type": f["resource"]["type"],
            "region": f["resource"]["region"],
            "monthly_cost_estimate": f["monthly_cost_estimate"],
            "reason": f["reason"],
            "remediation_command": f["remediation_command"],
            "created_at": f["created_at"],
        })
    cols = [
        "id", "severity", "detector", "resource_id", "provider", "resource_type",
        "region", "monthly_cost_estimate", "reason", "remediation_command", "created_at",
    ]
    return _stream_csv(rows, cols, "findings.csv")


@router.get("/resources.csv")
def export_resources(
    provider: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    include_released: bool = Query(True),
    session: Session = Depends(get_session),
):
    data = list_resources(
        provider=provider,
        resource_type=resource_type,
        account_id=account_id,
        is_inferred=None,
        include_released=include_released,
        status=None,
        search=None,
        sort="total_cost",
        order="desc",
        session=session,
    )
    rows = []
    for r in data["resources"]:
        rows.append({
            "resource_id": r["resource_id"],
            "provider": r["provider"],
            "resource_type": r["resource_type"],
            "region": r["region"],
            "account_id": r["account_id"],
            "state": r["state"],
            "is_inferred": r["is_inferred"],
            "first_seen_at": r["first_seen_at"],
            "last_seen_at": r["last_seen_at"],
            "total_cost": r["total_cost"],
            "open_findings_count": r["open_findings_count"],
            "released_count": r["released_count"],
        })
    cols = [
        "resource_id", "provider", "resource_type", "region", "account_id",
        "state", "is_inferred", "first_seen_at", "last_seen_at",
        "total_cost", "open_findings_count", "released_count",
    ]
    return _stream_csv(rows, cols, "resources.csv")


@router.get("/released.csv")
def export_released(session: Session = Depends(get_session)):
    released = session.query(ReleasedResource).order_by(ReleasedResource.released_at.desc()).all()
    rows = [r.to_dict() for r in released]
    cols = [
        "id", "released_at", "resource_id", "provider", "resource_type",
        "region", "account_id", "detector", "monthly_cost_saved",
        "remediation_command", "note",
    ]
    return _stream_csv(rows, cols, "released.csv")
