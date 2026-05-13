"""Idle load balancer detector (AWS ELB/ELBv2)."""
from __future__ import annotations

from typing import ClassVar, Iterable

from sqlalchemy.orm import Session

from ..models import Resource
from ..remediation import build_aws_command
from .base import DetectorResult, RuleSpec, estimate_monthly_cost


class IdleELBDetector:
    name = "idle_elb"
    severity = "medium"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="idle_elb",
        title="Idle Load Balancer",
        description=(
            "AWS classic ELB, ALB, or NLB instances that have served zero requests "
            "(or zero network bytes) over the past 7 days."
        ),
        providers=("aws",),
        resource_types=("ELB", "ALB", "NLB"),
        severity="medium",
        criteria=(
            "Provider is AWS",
            "Resource type is one of ELB / ALB / NLB",
            "request_avg_7d == 0 (falls back to net_avg_7d if requests missing)",
        ),
        requires=("inventory",),
        remediation_action="aws elbv2 delete-load-balancer / aws elb delete-load-balancer",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "aws",
            Resource.resource_type.in_(["ELB", "ALB", "NLB"]),
        )
        for r in q:
            reqs = r.request_avg_7d
            net = r.net_avg_7d
            traffic_signal = reqs if reqs is not None else net
            if traffic_signal is None or traffic_signal > 0:
                continue
            cost = estimate_monthly_cost(session, r.resource_id)
            cmd, notes = build_aws_command("delete_load_balancer", r)
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"Load balancer {r.resource_id} has handled no traffic over the "
                    f"last 7 days (request_avg_7d=0)."
                ),
                monthly_cost_estimate=cost,
                remediation_command=cmd,
                remediation_notes=notes,
            )
