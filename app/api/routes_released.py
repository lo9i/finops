"""Release-action endpoints (mark a finding fixed)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Finding, ReleasedResource

router = APIRouter(prefix="/api", tags=["released"])


class ReleaseBody(BaseModel):
    note: str | None = None


class BulkReleaseBody(BaseModel):
    finding_ids: list[int]
    note: str | None = None


@router.post("/findings/{finding_id}/release")
def release_finding(
    finding_id: int,
    body: ReleaseBody | None = None,
    session: Session = Depends(get_session),
):
    f = session.get(Finding, finding_id)
    if not f:
        raise HTTPException(status_code=404, detail="finding not found")

    existing = (
        session.query(ReleasedResource)
        .filter(
            ReleasedResource.resource_id == f.resource.resource_id,
            ReleasedResource.detector == f.detector,
        )
        .first()
    )
    if existing:
        # Already released — just delete the finding (it lingered) and return.
        session.delete(f)
        session.commit()
        return existing.to_dict()

    rel = ReleasedResource(
        resource_id=f.resource.resource_id,
        provider=f.resource.provider,
        resource_type=f.resource.resource_type,
        region=f.resource.region,
        account_id=f.resource.account_id,
        detector=f.detector,
        monthly_cost_saved=f.monthly_cost_estimate,
        remediation_command=f.remediation_command,
        note=(body.note if body else None),
    )
    session.add(rel)
    session.delete(f)
    session.commit()
    session.refresh(rel)
    return rel.to_dict()


@router.post("/findings/bulk-release")
def bulk_release(body: BulkReleaseBody, session: Session = Depends(get_session)):
    """Release multiple findings in one call. Returns per-id outcomes."""
    if not body.finding_ids:
        raise HTTPException(status_code=400, detail="finding_ids must not be empty")

    results: list[dict] = []
    total_saved = 0.0
    for fid in body.finding_ids:
        f = session.get(Finding, fid)
        if not f:
            results.append({"id": fid, "ok": False, "reason": "not found"})
            continue

        existing = (
            session.query(ReleasedResource)
            .filter(
                ReleasedResource.resource_id == f.resource.resource_id,
                ReleasedResource.detector == f.detector,
            )
            .first()
        )
        if existing:
            session.delete(f)
            results.append({"id": fid, "ok": True, "reason": "already released"})
            continue

        rel = ReleasedResource(
            resource_id=f.resource.resource_id,
            provider=f.resource.provider,
            resource_type=f.resource.resource_type,
            region=f.resource.region,
            account_id=f.resource.account_id,
            detector=f.detector,
            monthly_cost_saved=f.monthly_cost_estimate,
            remediation_command=f.remediation_command,
            note=body.note,
        )
        session.add(rel)
        session.delete(f)
        total_saved += f.monthly_cost_estimate
        results.append({"id": fid, "ok": True, "monthly_cost_saved": round(f.monthly_cost_estimate, 2)})

    session.commit()
    return {
        "results": results,
        "released_count": sum(1 for r in results if r["ok"]),
        "monthly_cost_saved": round(total_saved, 2),
    }


@router.get("/released")
def list_released(session: Session = Depends(get_session)):
    rows = (
        session.query(ReleasedResource)
        .order_by(ReleasedResource.released_at.desc())
        .all()
    )
    total = session.query(func.coalesce(func.sum(ReleasedResource.monthly_cost_saved), 0.0)).scalar() or 0.0
    return {
        "released": [r.to_dict() for r in rows],
        "total_monthly_saved": round(float(total), 2),
        "count": len(rows),
    }


@router.delete("/released/{released_id}")
def unrelease(released_id: int, session: Session = Depends(get_session)):
    rel = session.get(ReleasedResource, released_id)
    if not rel:
        raise HTTPException(status_code=404, detail="released entry not found")
    session.delete(rel)
    session.commit()
    return {"ok": True, "id": released_id}
