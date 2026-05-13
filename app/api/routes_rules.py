"""Rules introspection endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..detectors import get_rule, list_rules

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
def list_rules_api():
    return {"rules": [r.to_dict() for r in list_rules()]}


@router.get("/{slug}")
def get_rule_api(slug: str):
    spec = get_rule(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"rule '{slug}' not found")
    return spec.to_dict()
