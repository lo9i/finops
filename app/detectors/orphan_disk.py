"""Orphan disk detectors: AWS EBS + Azure managed disk."""
from __future__ import annotations

from typing import ClassVar, Iterable

from sqlalchemy.orm import Session

from ..models import Resource
from ..remediation import build_aws_command, build_azure_command
from .base import DetectorResult, RuleSpec, estimate_monthly_cost


class OrphanEBSVolumeDetector:
    name = "orphan_ebs_volume"
    severity = "high"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="orphan_ebs_volume",
        title="Orphan EBS Volume",
        description=(
            "AWS EBS volumes still allocated but not attached to any instance. "
            "Storage is charged per GB-month whether or not the volume is in use."
        ),
        providers=("aws",),
        resource_types=("EBS_VOLUME",),
        severity="high",
        criteria=(
            "Provider is AWS",
            "Resource type is EBS_VOLUME",
            "state == 'available' (case-insensitive)",
            "attachments is empty / null",
        ),
        requires=("inventory",),
        remediation_action="aws ec2 delete-volume (with --dry-run)",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "aws",
            Resource.resource_type == "EBS_VOLUME",
            Resource.is_inferred.is_(False),  # state-based: needs explicit inventory
        )
        for r in q:
            attached = bool(r.attachments)
            if (r.state or "").lower() == "available" and not attached:
                cost = estimate_monthly_cost(session, r.resource_id)
                cmd, notes = build_aws_command("delete_ebs_volume", r)
                yield DetectorResult(
                    resource=r,
                    detector=self.name,
                    severity=self.severity,
                    reason=(
                        f"EBS volume {r.resource_id} is unattached "
                        f"(state={r.state}). Still incurring storage charges."
                    ),
                    monthly_cost_estimate=cost,
                    remediation_command=cmd,
                    remediation_notes=notes,
                )


class OrphanAzureDiskDetector:
    name = "orphan_azure_disk"
    severity = "high"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="orphan_azure_disk",
        title="Orphan Azure Managed Disk",
        description=(
            "Azure managed disks in 'Unattached' state. The disk continues to incur "
            "storage cost until deleted."
        ),
        providers=("azure",),
        resource_types=("AZURE_DISK",),
        severity="high",
        criteria=(
            "Provider is Azure",
            "Resource type is AZURE_DISK",
            "state == 'Unattached' (case-insensitive)",
        ),
        requires=("inventory",),
        remediation_action="az disk delete",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "azure",
            Resource.resource_type == "AZURE_DISK",
            Resource.is_inferred.is_(False),
        )
        for r in q:
            if (r.state or "").lower() == "unattached":
                cost = estimate_monthly_cost(session, r.resource_id)
                cmd, notes = build_azure_command("delete_disk", r)
                yield DetectorResult(
                    resource=r,
                    detector=self.name,
                    severity=self.severity,
                    reason=(
                        f"Azure managed disk {r.resource_id} is unattached. "
                        "Continues to incur storage cost."
                    ),
                    monthly_cost_estimate=cost,
                    remediation_command=cmd,
                    remediation_notes=notes,
                )
