"""Per-resource detail endpoints + inventory listing + billing-history time series."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..db import get_session
from ..models import BillingRecord, Finding, Ingestion, ReleasedResource, Resource

router = APIRouter(prefix="/api/resources", tags=["resources"])


def _resource_dict(r: Resource) -> dict:
    return {
        "resource_id": r.resource_id,
        "provider": r.provider,
        "resource_type": r.resource_type,
        "region": r.region,
        "account_id": r.account_id,
        "state": r.state,
        "attachments": r.attachments,
        "tags": r.tags,
        "resource_group": r.resource_group,
        "cpu_avg_7d": r.cpu_avg_7d,
        "net_avg_7d": r.net_avg_7d,
        "request_avg_7d": r.request_avg_7d,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "is_inferred": r.is_inferred,
        "extra": r.extra,
    }


_SORT_KEYS = {
    "total_cost": lambda r: r["total_cost"],
    "first_seen_at": lambda r: r["first_seen_at"] or "",
    "last_seen_at": lambda r: r["last_seen_at"] or "",
    "resource_id": lambda r: r["resource_id"].lower(),
    "open_findings": lambda r: r["open_findings_count"],
}


@router.get("")
def list_resources(
    provider: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    is_inferred: Optional[bool] = Query(None),
    include_released: bool = Query(False, description="If false (default), hide resources with any release entry."),
    status: Optional[str] = Query(None, description="open | clean | released. Overrides include_released when set."),
    search: Optional[str] = Query(None, description="Substring match against resource_id (case-insensitive)."),
    sort: str = Query("total_cost", description=f"One of: {', '.join(_SORT_KEYS)}"),
    order: str = Query("desc", description="asc | desc"),
    session: Session = Depends(get_session),
):
    """Aggregated inventory listing — one row per resource with cost + findings + release status."""

    # Pre-compute aggregations in single queries to avoid N+1
    cost_rows = (
        session.query(
            BillingRecord.resource_id,
            func.coalesce(func.sum(BillingRecord.cost), 0.0).label("total_cost"),
        )
        .filter(BillingRecord.resource_id.isnot(None))
        .group_by(BillingRecord.resource_id)
        .all()
    )
    cost_by_rid = {r.resource_id: float(r.total_cost) for r in cost_rows}

    findings_by_rid: dict[str, list[dict]] = defaultdict(list)
    for f in session.query(Finding).join(Resource).all():
        findings_by_rid[f.resource.resource_id].append(
            {"detector": f.detector, "severity": f.severity}
        )

    released_rows = session.query(ReleasedResource).all()
    released_by_rid: dict[str, list[dict]] = defaultdict(list)
    for rel in released_rows:
        released_by_rid[rel.resource_id].append(
            {
                "detector": rel.detector,
                "released_at": rel.released_at.isoformat() if rel.released_at else None,
                "monthly_cost_saved": round(rel.monthly_cost_saved, 2),
            }
        )

    q = session.query(Resource)
    if provider:
        q = q.filter(Resource.provider == provider.lower())
    if resource_type:
        q = q.filter(Resource.resource_type == resource_type)
    if account_id:
        q = q.filter(Resource.account_id == account_id)
    if is_inferred is not None:
        q = q.filter(Resource.is_inferred.is_(is_inferred))
    if search:
        like = f"%{search}%"
        q = q.filter(Resource.resource_id.ilike(like))

    rows = q.all()

    # Normalize status / include_released interaction:
    #   status set    → use status filter, ignore include_released
    #   status unset  → apply include_released (default: hide released)
    status_norm = (status or "").lower() or None
    if status_norm not in (None, "open", "clean", "released"):
        status_norm = None

    out = []
    for r in rows:
        released_entries = released_by_rid.get(r.resource_id, [])
        is_released = bool(released_entries)
        open_count = len(findings_by_rid.get(r.resource_id, []))

        if status_norm == "open":
            if is_released or open_count == 0:
                continue
        elif status_norm == "clean":
            if is_released or open_count > 0:
                continue
        elif status_norm == "released":
            if not is_released:
                continue
        else:
            if is_released and not include_released:
                continue

        out.append(
            {
                **_resource_dict(r),
                "total_cost": round(cost_by_rid.get(r.resource_id, 0.0), 4),
                "open_findings": findings_by_rid.get(r.resource_id, []),
                "open_findings_count": open_count,
                "released_count": len(released_entries),
                "released_detectors": [e["detector"] for e in released_entries],
                "last_released_at": max((e["released_at"] for e in released_entries), default=None),
            }
        )

    sort_key = _SORT_KEYS.get(sort, _SORT_KEYS["total_cost"])
    out.sort(key=sort_key, reverse=(order != "asc"))

    # Build facets from the DB, but limit to providers the engine actually supports
    # (so we don't dangle e.g. "gcp" as a filter option when no detectors target it).
    provider_type_pairs = (
        session.query(Resource.provider, Resource.resource_type)
        .filter(Resource.provider.in_(config.SUPPORTED_PROVIDERS))
        .distinct()
        .all()
    )
    provider_types: dict[str, list[str]] = defaultdict(list)
    for p, t in provider_type_pairs:
        if t:
            provider_types[p].append(t)
    for p in provider_types:
        provider_types[p] = sorted(set(provider_types[p]))

    distinct_providers = sorted(
        p for p in provider_types.keys() if p in config.SUPPORTED_PROVIDERS
    )
    distinct_types = sorted({t for ts in provider_types.values() for t in ts})

    account_pairs = (
        session.query(Resource.provider, Resource.account_id)
        .filter(
            Resource.provider.in_(config.SUPPORTED_PROVIDERS),
            Resource.account_id.isnot(None),
        )
        .distinct()
        .all()
    )
    provider_accounts: dict[str, list[str]] = defaultdict(list)
    distinct_accounts: list[str] = []
    for p, a in account_pairs:
        provider_accounts[p].append(a)
        distinct_accounts.append(a)
    for p in provider_accounts:
        provider_accounts[p] = sorted(set(provider_accounts[p]))
    distinct_accounts = sorted(set(distinct_accounts))

    return {
        "resources": out,
        "count": len(out),
        "include_released": include_released,
        "filters": {
            "provider": provider,
            "resource_type": resource_type,
            "account_id": account_id,
            "is_inferred": is_inferred,
            "status": status_norm,
            "search": search,
            "sort": sort if sort in _SORT_KEYS else "total_cost",
            "order": order if order in ("asc", "desc") else "desc",
        },
        "facets": {
            "providers": distinct_providers,
            "resource_types": distinct_types,
            "provider_types": dict(provider_types),
            "accounts": distinct_accounts,
            "provider_accounts": dict(provider_accounts),
            "sorts": list(_SORT_KEYS),
            "supported_providers": list(config.SUPPORTED_PROVIDERS),
        },
        "total_known": session.query(func.count(Resource.id)).scalar() or 0,
    }


# NOTE: route order matters — `:path` is greedy. The more specific `/billing-history`
# variant must be registered before the catch-all detail route.

@router.get("/{resource_id:path}/billing-history")
def billing_history(resource_id: str, session: Session = Depends(get_session)):
    r = session.query(Resource).filter(Resource.resource_id == resource_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="resource not found")

    rows = (
        session.query(BillingRecord)
        .filter(BillingRecord.resource_id == resource_id)
        .order_by(BillingRecord.usage_start)
        .all()
    )
    by_day: dict[str, float] = defaultdict(float)
    by_usage_type: dict[str, float] = defaultdict(float)
    for b in rows:
        if b.usage_start is not None:
            by_day[b.usage_start.date().isoformat()] += b.cost
        by_usage_type[b.usage_type or "(unknown)"] += b.cost

    points = [{"date": d, "cost": round(c, 4)} for d, c in sorted(by_day.items())]
    return {
        "resource_id": resource_id,
        "points": points,
        "total_cost": round(sum(b.cost for b in rows), 4),
        "by_usage_type": {k: round(v, 4) for k, v in by_usage_type.items()},
    }


@router.get("/{resource_id:path}")
def get_resource(resource_id: str, session: Session = Depends(get_session)):
    r = session.query(Resource).filter(Resource.resource_id == resource_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="resource not found")

    findings = (
        session.query(Finding)
        .filter(Finding.resource_pk == r.id)
        .order_by(Finding.monthly_cost_estimate.desc())
        .all()
    )
    released = (
        session.query(ReleasedResource)
        .filter(ReleasedResource.resource_id == resource_id)
        .order_by(ReleasedResource.released_at.desc())
        .all()
    )

    billing_q = (
        session.query(BillingRecord)
        .filter(BillingRecord.resource_id == resource_id)
        .order_by(BillingRecord.usage_start)
        .all()
    )
    total_cost = sum(b.cost for b in billing_q)

    ingestion_ids = {b.ingestion_id for b in billing_q if b.ingestion_id}
    if r.ingestion_id:
        ingestion_ids.add(r.ingestion_id)
    ingestion_rows = (
        session.query(Ingestion).filter(Ingestion.id.in_(ingestion_ids)).all()
        if ingestion_ids
        else []
    )

    return {
        **_resource_dict(r),
        "total_cost": round(float(total_cost), 4),
        "billing_rows_count": len(billing_q),
        "findings": [f.to_dict() for f in findings],
        "released": [rel.to_dict() for rel in released],
        "ingestions": [i.to_dict() for i in ingestion_rows],
    }
