"""Dashboard (Jinja2): sidebar + pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_session
from ..detectors import list_rules
from ..models import Ingestion, ReleasedResource, Resource
from .routes_findings import build_summary, query_findings
from .routes_ingestions import get_ingestion as ingestion_detail
from .routes_resources import get_resource as resource_detail_api
from .routes_resources import list_resources as list_resources_api

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "dashboard" / "templates")
)

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)):
    summary = build_summary(session)
    findings = query_findings(session)
    latest = (
        session.query(Ingestion)
        .order_by(Ingestion.uploaded_at.desc())
        .limit(5)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_nav": "home",
            "summary": summary,
            "findings": findings,
            "latest_ingestions": [i.to_dict() for i in latest],
        },
    )


@router.get("/ingestions", response_class=HTMLResponse)
def ingestions_page(request: Request, session: Session = Depends(get_session)):
    rows = session.query(Ingestion).order_by(Ingestion.uploaded_at.desc()).all()
    return templates.TemplateResponse(
        request,
        "ingestions.html",
        {"active_nav": "ingestions", "ingestions": [r.to_dict() for r in rows]},
    )


@router.get("/ingestions/{ingestion_id}", response_class=HTMLResponse)
def ingestion_detail_page(
    ingestion_id: int, request: Request, session: Session = Depends(get_session)
):
    ing = session.get(Ingestion, ingestion_id)
    if not ing:
        raise HTTPException(status_code=404, detail="ingestion not found")
    data = ingestion_detail(ingestion_id=ingestion_id, session=session)
    return templates.TemplateResponse(
        request,
        "ingestion_detail.html",
        {"active_nav": "ingestions", "ing": data},
    )


@router.get("/released", response_class=HTMLResponse)
def released_page(request: Request, session: Session = Depends(get_session)):
    rows = (
        session.query(ReleasedResource)
        .order_by(ReleasedResource.released_at.desc())
        .all()
    )
    total = sum(r.monthly_cost_saved for r in rows)
    return templates.TemplateResponse(
        request,
        "released.html",
        {
            "active_nav": "released",
            "released": [r.to_dict() for r in rows],
            "total_monthly_saved": round(total, 2),
            "count": len(rows),
        },
    )


@router.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    rules = [r.to_dict() for r in list_rules()]
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"active_nav": "rules", "rules": rules},
    )


@router.get("/inventory", response_class=HTMLResponse)
def inventory_page(
    request: Request,
    show_released: bool = False,
    provider: str | None = None,
    resource_type: str | None = None,
    account_id: str | None = None,
    source: str | None = None,            # "inventory" | "inferred"
    status: str | None = None,
    q: str | None = None,                  # search term
    sort: str = "total_cost",
    order: str = "desc",
    session: Session = Depends(get_session),
):
    is_inferred = None
    if source == "inferred":
        is_inferred = True
    elif source == "inventory":
        is_inferred = False

    data = list_resources_api(
        provider=provider or None,
        resource_type=resource_type or None,
        account_id=account_id or None,
        is_inferred=is_inferred,
        include_released=show_released,
        status=status or None,
        search=q or None,
        sort=sort,
        order=order,
        session=session,
    )
    released_rids = {r.resource_id for r in session.query(ReleasedResource).all()}
    return templates.TemplateResponse(
        request,
        "inventory.html",
        {
            "active_nav": "inventory",
            "resources": data["resources"],
            "count": data["count"],
            "total_known": data["total_known"],
            "show_released": show_released,
            "released_resource_count": len(released_rids),
            "filters": data["filters"],
            "facets": data["facets"],
            "source_filter": source or "",
            "q": q or "",
        },
    )


@router.get("/resources/{resource_id:path}", response_class=HTMLResponse)
def resource_detail_page(
    resource_id: str, request: Request, session: Session = Depends(get_session)
):
    r = session.query(Resource).filter(Resource.resource_id == resource_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="resource not found")
    data = resource_detail_api(resource_id=resource_id, session=session)
    return templates.TemplateResponse(
        request,
        "resource_detail.html",
        {"active_nav": "", "r": data},
    )
