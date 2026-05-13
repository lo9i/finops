"""History-based detector: long-running, unmonitored billed resource.

Fires when:
  - A resource has been billed for >= UNMONITORED_MIN_DAYS (first_seen_at long ago), AND
  - It is still costing money in the last 30 days (>= UNMONITORED_MIN_RECENT_COST), AND
  - It is inferred-only (no inventory upload has confirmed its state).

The point: surface resources that have been quietly billing for weeks while we
have no idea what they're doing. The remediation is "investigate or upload
inventory", not a direct CLI command.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import config
from ..models import BillingRecord, Resource
from .base import DetectorResult, RuleSpec, ThresholdSpec


_MIN_DAYS = ThresholdSpec(
    name="min_days_billed",
    description="Minimum days since first billing record for the resource.",
    config_attr="UNMONITORED_MIN_DAYS",
    unit="days",
)
_MIN_COST = ThresholdSpec(
    name="min_recent_cost",
    description="Minimum cost charged in the last 30 days to flag the resource.",
    config_attr="UNMONITORED_MIN_RECENT_COST",
    unit="USD",
)


class UnmonitoredLongRunningDetector:
    name = "unmonitored_long_running"
    severity = "low"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="unmonitored_long_running",
        title="Unmonitored Long-Running Resource",
        description=(
            "Resource has been incurring charges for many weeks and we still don't "
            "have an inventory record for it. Likely candidate to investigate or "
            "upload state for so the dedicated detectors can run."
        ),
        providers=("aws", "azure"),
        resource_types=("(any)",),
        severity="low",
        criteria=(
            "is_inferred (no inventory upload has confirmed the resource)",
            "first_seen_at older than min_days_billed",
            "Total cost in last 30 days >= min_recent_cost",
        ),
        requires=("billing",),
        thresholds=(_MIN_DAYS, _MIN_COST),
        remediation_action="Upload an inventory snapshot for richer detection, or audit the resource manually.",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        now = datetime.utcnow()
        days_cutoff = now - timedelta(days=config.UNMONITORED_MIN_DAYS)
        recent_cutoff = now - timedelta(days=30)

        candidates = (
            session.query(Resource)
            .filter(
                Resource.is_inferred.is_(True),
                Resource.first_seen_at.isnot(None),
                Resource.first_seen_at <= days_cutoff,
            )
            .all()
        )

        for r in candidates:
            recent_cost = (
                session.query(func.coalesce(func.sum(BillingRecord.cost), 0.0))
                .filter(
                    BillingRecord.resource_id == r.resource_id,
                    BillingRecord.usage_start >= recent_cutoff,
                )
                .scalar()
                or 0.0
            )
            if float(recent_cost) < config.UNMONITORED_MIN_RECENT_COST:
                continue
            age_days = (now - r.first_seen_at).days
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"{r.resource_id} ({r.resource_type}) has been billed for {age_days} days "
                    f"(first seen {r.first_seen_at.date().isoformat()}); recent 30-day cost "
                    f"= ${float(recent_cost):.2f}. No inventory upload has confirmed its state."
                ),
                monthly_cost_estimate=float(recent_cost),
                remediation_command=(
                    f"# investigate: upload inventory or audit\n"
                    f"# resource_id={r.resource_id}\n"
                    f"# provider={r.provider} type={r.resource_type} region={r.region or '?'}"
                ),
                remediation_notes=(
                    "This finding doesn't carry a destructive command — the engine "
                    "doesn't know the resource state. Upload an inventory file (or look "
                    "at the resource page) to enable the type-specific detectors."
                ),
            )
