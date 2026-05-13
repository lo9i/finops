"""Build Resource rows from billing line items.

Real CUR/Cost-Management exports don't carry state, but they DO carry resource_id
plus usage_type / service / region. That's enough to seed a partial inventory
(resource_type, first_seen_at, last_seen_at, sometimes state) so that:

  - history-based detectors can fire without an explicit inventory upload, and
  - the Resources page has a meaningful entry for every billed resource.

Rules:
  - Explicit inventory uploads remain authoritative (is_inferred stays False; the
    state we set is preserved).
  - Inferred resources can later be "promoted" to explicit when a matching inventory
    row arrives — the upserter in loader.py handles that.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import BillingRecord, Ingestion, Resource


def _infer_type_and_state(
    provider: str, usage_type: str, service: str, resource_id: str
) -> tuple[str, Optional[str], dict]:
    """Returns (resource_type, state, extra)."""
    ut = (usage_type or "")
    svc = (service or "")
    if provider == "aws":
        if ut.startswith("EBS:VolumeUsage"):
            return "EBS_VOLUME", None, {}
        if ut.startswith("EBS:SnapshotUsage"):
            return "EBS_SNAPSHOT", "completed", {}
        if ut.startswith("BoxUsage:") or ut.startswith("SpotUsage:") or ut.startswith("DedicatedUsage:"):
            return "EC2_INSTANCE", "running", {}
        if ut == "LoadBalancerUsage" or ut.startswith("LoadBalancerUsage"):
            if "/app/" in resource_id:
                return "ALB", "active", {}
            if "/net/" in resource_id:
                return "NLB", "active", {}
            return "ELB", "active", {}
        if ut == "ElasticIP:IdleAddress":
            return "ELASTIC_IP", "available", {"billing_inferred_idle": True}
        if ut == "ElasticIP:InUseAddress":
            return "ELASTIC_IP", "in-use", {}
        if "Block Store" in svc:
            return "EBS_VOLUME", None, {}
        if "Compute Cloud" in svc:
            return "EC2_INSTANCE", "running", {}
        if "Load Balancing" in svc:
            return "ALB" if "/app/" in resource_id else "ELB", "active", {}
        return "AWS_UNKNOWN", None, {}

    if provider == "azure":
        if "Managed Disks" in svc or "Managed Disks" in ut:
            return "AZURE_DISK", None, {}
        if svc == "Virtual Machines" or "Virtual Machines" in ut:
            return "AZURE_VM", "PowerState/running", {}
        return "AZURE_UNKNOWN", None, {}

    return "UNKNOWN", None, {}


def infer_resources_from_billing(session: Session, ingestion: Ingestion) -> int:
    """Upsert Resource rows for every distinct resource_id in this ingestion's billing.

    Returns the count of resources touched (created + updated).
    """
    rows = (
        session.query(BillingRecord)
        .filter(
            BillingRecord.ingestion_id == ingestion.id,
            BillingRecord.resource_id.isnot(None),
        )
        .all()
    )

    by_rid: dict[str, list[BillingRecord]] = defaultdict(list)
    for r in rows:
        by_rid[r.resource_id].append(r)

    touched = 0
    for rid, brs in by_rid.items():
        # First/last seen from usage_start (fallback to ingestion time if none recorded)
        seen_dates = [b.usage_start for b in brs if b.usage_start is not None]
        first_seen = min(seen_dates) if seen_dates else ingestion.uploaded_at
        last_seen = max(seen_dates) if seen_dates else ingestion.uploaded_at

        # Pick most-informative usage_type (longest non-empty) to drive inference
        sample = max(brs, key=lambda b: len(b.usage_type or ""))
        rtype, inferred_state, extra = _infer_type_and_state(
            sample.provider, sample.usage_type or "", sample.service or "", rid
        )
        region = next((b.region for b in brs if b.region), "")
        account_id = next((b.account_id for b in brs if b.account_id), None)

        existing = session.query(Resource).filter(Resource.resource_id == rid).first()
        if existing is None:
            session.add(
                Resource(
                    resource_id=rid,
                    provider=sample.provider,
                    resource_type=rtype,
                    region=region or "",
                    state=inferred_state,
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                    is_inferred=True,
                    extra=extra or None,
                    account_id=account_id,
                )
            )
        else:
            # Always expand the window; never shrink it.
            if first_seen and (existing.first_seen_at is None or first_seen < existing.first_seen_at):
                existing.first_seen_at = first_seen
            if last_seen and (existing.last_seen_at is None or last_seen > existing.last_seen_at):
                existing.last_seen_at = last_seen
            # Only fill in fields if the resource was inferred AND the field is empty.
            if existing.is_inferred:
                if not existing.resource_type or existing.resource_type.endswith("_UNKNOWN"):
                    existing.resource_type = rtype
                if not existing.state and inferred_state:
                    existing.state = inferred_state
                if not existing.region and region:
                    existing.region = region
                if extra:
                    merged = dict(existing.extra or {})
                    merged.update(extra)
                    existing.extra = merged
                if account_id and not existing.account_id:
                    existing.account_id = account_id
        touched += 1

    session.flush()
    return touched
