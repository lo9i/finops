"""Registry of all detectors + run_all + rule introspection."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import DetectionRun, Finding, ReleasedResource
from .base import Detector, DetectorResult, RuleSpec
from .idle_eip_billing import IdleEIPByBillingDetector
from .idle_lb import IdleELBDetector
from .idle_vm import IdleAzureVMDetector, IdleEC2Detector
from .old_snapshot import OldEBSSnapshotDetector
from .orphan_disk import OrphanAzureDiskDetector, OrphanEBSVolumeDetector
from .unassociated_eip import UnassociatedEIPDetector
from .unmonitored_long_running import UnmonitoredLongRunningDetector

ALL_DETECTORS: list[Detector] = [
    OrphanEBSVolumeDetector(),
    OrphanAzureDiskDetector(),
    IdleEC2Detector(),
    IdleAzureVMDetector(),
    OldEBSSnapshotDetector(),
    IdleELBDetector(),
    UnassociatedEIPDetector(),
    IdleEIPByBillingDetector(),
    UnmonitoredLongRunningDetector(),
]


def list_rules() -> list[RuleSpec]:
    return [d.SPEC for d in ALL_DETECTORS]


def get_rule(slug: str) -> RuleSpec | None:
    for d in ALL_DETECTORS:
        if d.SPEC.slug == slug:
            return d.SPEC
    return None


def _released_set(session: Session) -> set[tuple[str, str]]:
    return {
        (r.resource_id, r.detector)
        for r in session.query(ReleasedResource).all()
    }


def run_all(
    session: Session,
    *,
    ingestion_id: Optional[int] = None,
    trigger: str = "manual",
) -> DetectionRun:
    run = DetectionRun(
        ingestion_id=ingestion_id,
        started_at=datetime.utcnow(),
        trigger=trigger,
    )
    session.add(run)
    session.flush()

    session.query(Finding).delete()
    session.flush()

    released = _released_set(session)

    # Dedupe so two detectors don't both file findings for the same (resource, slug).
    # We allow ONE finding per resource per detector slug; the same resource may have
    # findings from multiple detectors (e.g., orphan + unmonitored).
    seen: set[tuple[str, str]] = set()

    count = 0
    waste = 0.0
    for det in ALL_DETECTORS:
        for result in det.find(session):
            key = (result.resource.resource_id, result.detector)
            if key in released or key in seen:
                continue
            seen.add(key)
            session.add(_to_model(result, run.id))
            count += 1
            waste += result.monthly_cost_estimate

    run.findings_count = count
    run.monthly_waste = waste
    run.finished_at = datetime.utcnow()
    session.flush()
    return run


def _to_model(r: DetectorResult, run_id: int) -> Finding:
    return Finding(
        resource_pk=r.resource.id,
        detection_run_id=run_id,
        detector=r.detector,
        severity=r.severity,
        monthly_cost_estimate=r.monthly_cost_estimate,
        reason=r.reason,
        remediation_command=r.remediation_command,
        remediation_notes=r.remediation_notes,
    )
