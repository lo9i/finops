"""Ingestion history endpoints."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import BillingRecord, DetectionRun, Finding, Ingestion, Resource

router = APIRouter(prefix="/api/ingestions", tags=["ingestions"])


@router.get("")
def list_ingestions(session: Session = Depends(get_session)):
    rows = session.query(Ingestion).order_by(Ingestion.uploaded_at.desc()).all()
    return {"ingestions": [r.to_dict() for r in rows]}


@router.get("/{ingestion_id}")
def get_ingestion(ingestion_id: int, session: Session = Depends(get_session)):
    ing = session.get(Ingestion, ingestion_id)
    if not ing:
        raise HTTPException(status_code=404, detail="ingestion not found")

    runs = (
        session.query(DetectionRun)
        .filter(DetectionRun.ingestion_id == ingestion_id)
        .order_by(DetectionRun.started_at.desc())
        .all()
    )

    latest_run_findings: list[dict] = []
    if runs:
        latest_run_findings = [
            f.to_dict()
            for f in session.query(Finding)
            .filter(Finding.detection_run_id == runs[0].id)
            .order_by(Finding.monthly_cost_estimate.desc())
            .all()
        ]

    if ing.kind == "inventory":
        # Build a resource_id -> [findings] map for ALL current open findings,
        # so the resource preview can show which rule(s) matched each row.
        findings_by_rid: dict[str, list[dict]] = defaultdict(list)
        for f in session.query(Finding).join(Resource).all():
            findings_by_rid[f.resource.resource_id].append(
                {
                    "id": f.id,
                    "detector": f.detector,
                    "severity": f.severity,
                    "monthly_cost_estimate": round(f.monthly_cost_estimate, 2),
                    "reason": f.reason,
                    "remediation_command": f.remediation_command,
                }
            )

        sample = []
        for r in (
            session.query(Resource)
            .filter(Resource.ingestion_id == ingestion_id)
            .limit(50)
            .all()
        ):
            sample.append(
                {
                    "resource_id": r.resource_id,
                    "provider": r.provider,
                    "type": r.resource_type,
                    "region": r.region,
                    "state": r.state,
                    "findings": findings_by_rid.get(r.resource_id, []),
                }
            )
    else:
        sample = [
            {
                "resource_id": b.resource_id,
                "service": b.service,
                "cost": round(b.cost, 4),
                "currency": b.currency,
                "region": b.region,
                "usage_start": b.usage_start.isoformat() if b.usage_start else None,
            }
            for b in session.query(BillingRecord)
            .filter(BillingRecord.ingestion_id == ingestion_id)
            .limit(50)
            .all()
        ]

    return {
        **ing.to_dict(),
        "detection_runs": [r.to_dict() for r in runs],
        "findings": latest_run_findings,
        "sample": sample,
    }
