"""Unassociated Elastic IP detector (AWS)."""
from __future__ import annotations

from typing import ClassVar, Iterable

from sqlalchemy.orm import Session

from ..models import Resource
from ..remediation import build_aws_command
from .base import DetectorResult, RuleSpec, estimate_monthly_cost


class UnassociatedEIPDetector:
    name = "unassociated_eip"
    severity = "low"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="unassociated_eip",
        title="Unassociated Elastic IP",
        description=(
            "Elastic IP addresses that are allocated to the account but not associated "
            "with any running instance. AWS charges for idle EIPs."
        ),
        providers=("aws",),
        resource_types=("ELASTIC_IP",),
        severity="low",
        criteria=(
            "Provider is AWS",
            "Resource type is ELASTIC_IP",
            "attachments is empty / null",
        ),
        requires=("inventory",),
        remediation_action="aws ec2 release-address (with --dry-run)",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "aws",
            Resource.resource_type == "ELASTIC_IP",
            Resource.is_inferred.is_(False),  # see idle_eip_by_billing for the inferred path
        )
        for r in q:
            if r.attachments:
                continue
            cost = estimate_monthly_cost(session, r.resource_id)
            cmd, notes = build_aws_command("release_elastic_ip", r)
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"Elastic IP {r.resource_id} is allocated but not associated with any "
                    "running instance. AWS charges for idle EIPs."
                ),
                monthly_cost_estimate=cost,
                remediation_command=cmd,
                remediation_notes=notes,
            )
