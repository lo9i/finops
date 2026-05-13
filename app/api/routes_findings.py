"""Findings + summary endpoints."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import BillingRecord, DetectionRun, Finding, Ingestion, ReleasedResource, Resource

router = APIRouter(prefix="/api", tags=["findings"])


def query_findings(
    session: Session,
    *,
    provider: Optional[str] = None,
    severity: Optional[str] = None,
    detector: Optional[str] = None,
) -> list[dict]:
    q = session.query(Finding).join(Resource)
    if provider:
        q = q.filter(Resource.provider == provider.lower())
    if severity:
        q = q.filter(Finding.severity == severity.lower())
    if detector:
        q = q.filter(Finding.detector == detector)
    q = q.order_by(Finding.monthly_cost_estimate.desc())
    return [f.to_dict() for f in q.all()]


def build_summary(session: Session) -> dict:
    findings = session.query(Finding).join(Resource).all()
    total_waste = round(sum(f.monthly_cost_estimate for f in findings), 2)

    by_provider: dict[str, float] = defaultdict(float)
    by_detector: dict[str, dict] = defaultdict(lambda: {"count": 0, "monthly_cost": 0.0})
    by_severity: dict[str, int] = defaultdict(int)
    by_service: dict[str, float] = defaultdict(float)
    by_account: dict[str, float] = defaultdict(float)

    for f in findings:
        by_provider[f.resource.provider] += f.monthly_cost_estimate
        by_detector[f.detector]["count"] += 1
        by_detector[f.detector]["monthly_cost"] += f.monthly_cost_estimate
        by_severity[f.severity] += 1
        by_service[f.resource.resource_type] += f.monthly_cost_estimate
        if f.resource.account_id:
            by_account[f.resource.account_id] += f.monthly_cost_estimate

    total_billed = (
        session.query(func.coalesce(func.sum(BillingRecord.cost), 0.0)).scalar() or 0.0
    )
    total_saved = (
        session.query(func.coalesce(func.sum(ReleasedResource.monthly_cost_saved), 0.0)).scalar() or 0.0
    )
    released_count = session.query(func.count(ReleasedResource.id)).scalar() or 0
    ingestion_count = session.query(func.count(Ingestion.id)).scalar() or 0
    latest_run = (
        session.query(DetectionRun).order_by(DetectionRun.started_at.desc()).first()
    )

    return {
        "findings_count": len(findings),
        "total_monthly_waste": total_waste,
        "total_billed_ingested": round(float(total_billed), 2),
        "total_monthly_saved": round(float(total_saved), 2),
        "released_count": int(released_count),
        "ingestion_count": int(ingestion_count),
        "latest_detection_run": latest_run.to_dict() if latest_run else None,
        "by_provider": {k: round(v, 2) for k, v in by_provider.items()},
        "by_detector": {
            k: {"count": v["count"], "monthly_cost": round(v["monthly_cost"], 2)}
            for k, v in by_detector.items()
        },
        "by_severity": dict(by_severity),
        "by_resource_type": {k: round(v, 2) for k, v in by_service.items()},
        "by_account": {k: round(v, 2) for k, v in by_account.items()},
    }


@router.get("/findings")
def list_findings(
    provider: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    detector: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    return {
        "findings": query_findings(
            session, provider=provider, severity=severity, detector=detector
        )
    }


@router.get("/findings/{finding_id}")
def get_finding(finding_id: int, session: Session = Depends(get_session)):
    f = session.get(Finding, finding_id)
    if not f:
        raise HTTPException(status_code=404, detail="finding not found")
    return f.to_dict()


@router.get("/summary")
def summary(session: Session = Depends(get_session)):
    return build_summary(session)


# /api/resources moved to routes_resources.py (full per-resource detail + billing history).
