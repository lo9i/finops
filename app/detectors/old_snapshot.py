"""Stale snapshot detector (AWS EBS)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import ClassVar, Iterable

from sqlalchemy.orm import Session

from .. import config
from ..models import Resource
from ..remediation import build_aws_command
from .base import DetectorResult, RuleSpec, ThresholdSpec, estimate_monthly_cost


_AGE_THRESHOLD = ThresholdSpec(
    name="max_age_days",
    description="Snapshots older than this are flagged as stale.",
    config_attr="OLD_SNAPSHOT_DAYS",
    unit="days",
)


class OldEBSSnapshotDetector:
    name = "old_ebs_snapshot"
    severity = "low"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="old_ebs_snapshot",
        title="Old EBS Snapshot",
        description=(
            "EBS snapshots older than the retention threshold. Snapshots accumulate "
            "and rarely get cleaned up — common low-hanging waste."
        ),
        providers=("aws",),
        resource_types=("EBS_SNAPSHOT",),
        severity="low",
        criteria=(
            "Provider is AWS",
            "Resource type is EBS_SNAPSHOT",
            "created_at is more than max_age_days ago",
        ),
        requires=("inventory",),
        thresholds=(_AGE_THRESHOLD,),
        remediation_action="aws ec2 delete-snapshot (with --dry-run)",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        cutoff = datetime.utcnow() - timedelta(days=config.OLD_SNAPSHOT_DAYS)
        q = session.query(Resource).filter(
            Resource.provider == "aws",
            Resource.resource_type == "EBS_SNAPSHOT",
        )
        for r in q:
            if r.created_at is None or r.created_at >= cutoff:
                continue
            age_days = (datetime.utcnow() - r.created_at).days
            cost = estimate_monthly_cost(session, r.resource_id)
            cmd, notes = build_aws_command("delete_ebs_snapshot", r)
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"Snapshot {r.resource_id} is {age_days} days old "
                    f"(> {config.OLD_SNAPSHOT_DAYS}-day threshold)."
                ),
                monthly_cost_estimate=cost,
                remediation_command=cmd,
                remediation_notes=notes,
            )
