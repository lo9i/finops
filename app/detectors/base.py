"""Detector protocol + RuleSpec metadata + monthly cost estimator."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Iterable, Protocol

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..models import BillingRecord, Resource


# ---------- Rule metadata ----------

@dataclass(frozen=True)
class ThresholdSpec:
    """Configurable threshold the operator can override via env var."""
    name: str
    description: str
    config_attr: str
    unit: str = ""

    def current_value(self) -> Any:
        return getattr(config, self.config_attr)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "config_attr": self.config_attr,
            "env_var": self.config_attr,  # config attrs and env vars share names here
            "unit": self.unit,
            "current_value": self.current_value(),
        }


@dataclass(frozen=True)
class RuleSpec:
    slug: str
    title: str
    description: str
    providers: tuple[str, ...]
    resource_types: tuple[str, ...]
    severity: str
    criteria: tuple[str, ...]
    remediation_action: str
    # What data this rule REQUIRES to fire — any combination of "billing", "inventory".
    # Used by the Rules UI to badge each detector and by users to plan uploads.
    requires: tuple[str, ...] = ("inventory",)
    thresholds: tuple[ThresholdSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "providers": list(self.providers),
            "resource_types": list(self.resource_types),
            "severity": self.severity,
            "criteria": list(self.criteria),
            "remediation_action": self.remediation_action,
            "requires": list(self.requires),
            "thresholds": [t.to_dict() for t in self.thresholds],
        }


# ---------- Detector protocol ----------

@dataclass
class DetectorResult:
    resource: Resource
    detector: str
    severity: str
    reason: str
    monthly_cost_estimate: float
    remediation_command: str
    remediation_notes: str


class Detector(Protocol):
    SPEC: ClassVar[RuleSpec]

    @property
    def name(self) -> str: ...

    @property
    def severity(self) -> str: ...

    def find(self, session: Session) -> Iterable[DetectorResult]: ...


# ---------- Shared cost estimator ----------

def estimate_monthly_cost(session: Session, resource_id: str | None) -> float:
    """
    Sum billing for resource over the last 30 days; if no rows fall in window,
    fall back to summing all rows referencing the resource.
    """
    if not resource_id:
        return 0.0

    cutoff = datetime.utcnow() - timedelta(days=30)
    q = session.query(func.coalesce(func.sum(BillingRecord.cost), 0.0)).filter(
        BillingRecord.resource_id == resource_id
    )

    windowed = q.filter(BillingRecord.usage_start >= cutoff).scalar() or 0.0
    if windowed > 0:
        return float(windowed)

    total = q.scalar() or 0.0
    return float(total)
