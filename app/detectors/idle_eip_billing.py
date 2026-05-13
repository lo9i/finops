"""Billing-only detector: idle Elastic IP via CUR usage type.

AWS encodes idle-EIP state in the usage type itself (`ElasticIP:IdleAddress`
vs `ElasticIP:InUseAddress`). This is one of the few cases where billing alone
gives a definitive orphan signal — no inventory needed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import BillingRecord, Resource
from ..remediation import build_aws_command
from .base import DetectorResult, RuleSpec, estimate_monthly_cost


class IdleEIPByBillingDetector:
    name = "idle_eip_by_billing"
    severity = "low"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="idle_eip_by_billing",
        title="Idle Elastic IP (billing signal)",
        description=(
            "Detects unassociated Elastic IPs directly from billing data — AWS marks "
            "the line item with usage type 'ElasticIP:IdleAddress'. Works without any "
            "inventory upload."
        ),
        providers=("aws",),
        resource_types=("ELASTIC_IP",),
        severity="low",
        criteria=(
            "BillingRecord.provider == 'aws'",
            "BillingRecord.usage_type starts with 'ElasticIP:IdleAddress'",
            "Charge recorded in the last 30 days",
        ),
        requires=("billing",),
        remediation_action="aws ec2 release-address (with --dry-run)",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        cutoff = datetime.utcnow() - timedelta(days=30)
        q = (
            session.query(
                BillingRecord.resource_id,
                func.coalesce(func.sum(BillingRecord.cost), 0.0).label("recent_cost"),
            )
            .filter(
                BillingRecord.provider == "aws",
                BillingRecord.usage_type.like("ElasticIP:IdleAddress%"),
                BillingRecord.resource_id.isnot(None),
                BillingRecord.usage_start >= cutoff,
            )
            .group_by(BillingRecord.resource_id)
        )

        for rid, recent_cost in q.all():
            r = (
                session.query(Resource)
                .filter(Resource.resource_id == rid)
                .first()
            )
            if r is None:
                continue
            cost = float(recent_cost) or estimate_monthly_cost(session, rid)
            cmd, notes = build_aws_command("release_elastic_ip", r)
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"Elastic IP {rid} appears in billing with usage_type "
                    f"'ElasticIP:IdleAddress' in the last 30 days "
                    f"(${recent_cost:.2f} charged) — AWS itself classifies it as idle."
                ),
                monthly_cost_estimate=cost,
                remediation_command=cmd,
                remediation_notes=notes,
            )
