"""SQLAlchemy ORM models.

Schema:
  - resources           : normalized inventory across clouds
  - billing_records     : normalized billing line items
  - ingestions          : every uploaded file (status + warnings)
  - detection_runs      : each detector pass (linked to the ingestion that triggered it)
  - findings            : open waste detected by the most recent run
  - released_resources  : user-confirmed remediation history
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(64))
    state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    attachments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cpu_avg_7d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_avg_7d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    request_avg_7d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    resource_group: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ingestion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestions.id"), nullable=True, index=True
    )
    # Cloud account / subscription this resource belongs to. AWS: 12-digit Account ID
    # (lineItem/UsageAccountId in CUR). Azure: subscription GUID.
    account_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Provenance: was this resource created from explicit inventory upload or inferred from billing?
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    # Time-history fields populated from billing rows.
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    findings: Mapped[list["Finding"]] = relationship(
        "Finding", back_populates="resource", cascade="all, delete-orphan"
    )


class BillingRecord(Base):
    __tablename__ = "billing_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(256), index=True, nullable=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    usage_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    usage_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    region: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    usage_type: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ingestion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestions.id"), nullable=True, index=True
    )
    account_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("ix_billing_resource_start", "resource_id", "usage_start"),
    )


class Ingestion(Base):
    """One uploaded file."""

    __tablename__ = "ingestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(32), index=True)              # "billing" | "inventory"
    detected_format: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detected_provider: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_ingested: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="success")     # success|partial|failed
    # Lifecycle independent of status: queued → processing → done|failed.
    # Lets the UI show a spinner / progress while a large file is being parsed.
    processing_state: Mapped[str] = mapped_column(String(16), default="done", index=True)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    detection_runs: Mapped[list["DetectionRun"]] = relationship(
        "DetectionRun", back_populates="ingestion", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict[str, Any]:
        latest = self.detection_runs[-1] if self.detection_runs else None
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "detected_format": self.detected_format,
            "detected_provider": self.detected_provider,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "rows_total": self.rows_total,
            "rows_ingested": self.rows_ingested,
            "rows_skipped": self.rows_skipped,
            "status": self.status,
            "warnings": self.warnings or [],
            "error_message": self.error_message,
            "size_bytes": self.size_bytes,
            "processing_state": self.processing_state,
            "rows_processed": self.rows_processed,
            "latest_detection_run": latest.to_dict() if latest else None,
        }


class DetectionRun(Base):
    """One detector pass — usually auto-triggered by an Ingestion."""

    __tablename__ = "detection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingestion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestions.id"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    monthly_waste: Mapped[float] = mapped_column(Float, default=0.0)
    trigger: Mapped[str] = mapped_column(String(32), default="ingest")  # ingest|manual

    ingestion: Mapped[Optional[Ingestion]] = relationship("Ingestion", back_populates="detection_runs")
    findings: Mapped[list["Finding"]] = relationship("Finding", back_populates="detection_run")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ingestion_id": self.ingestion_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "findings_count": self.findings_count,
            "monthly_waste": round(self.monthly_waste, 2),
            "trigger": self.trigger,
        }


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_pk: Mapped[int] = mapped_column(ForeignKey("resources.id"), index=True)
    detection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("detection_runs.id"), nullable=True, index=True
    )
    detector: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    monthly_cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text)
    remediation_command: Mapped[str] = mapped_column(Text)
    remediation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    resource: Mapped[Resource] = relationship("Resource", back_populates="findings")
    detection_run: Mapped[Optional[DetectionRun]] = relationship("DetectionRun", back_populates="findings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "detector": self.detector,
            "severity": self.severity,
            "monthly_cost_estimate": round(self.monthly_cost_estimate, 2),
            "reason": self.reason,
            "remediation_command": self.remediation_command,
            "remediation_notes": self.remediation_notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "detection_run_id": self.detection_run_id,
            "resource": {
                "id": self.resource.resource_id,
                "provider": self.resource.provider,
                "type": self.resource.resource_type,
                "region": self.resource.region,
                "state": self.resource.state,
                "tags": self.resource.tags,
            },
        }


class ReleasedResource(Base):
    """User-confirmed remediation — resource fixed/released."""

    __tablename__ = "released_resources"
    __table_args__ = (UniqueConstraint("resource_id", "detector", name="uq_released_resource_detector"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(256), index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    region: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    detector: Mapped[str] = mapped_column(String(64))
    monthly_cost_saved: Mapped[float] = mapped_column(Float, default=0.0)
    remediation_command: Mapped[str] = mapped_column(Text)
    released_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "provider": self.provider,
            "resource_type": self.resource_type,
            "region": self.region,
            "account_id": self.account_id,
            "detector": self.detector,
            "monthly_cost_saved": round(self.monthly_cost_saved, 2),
            "remediation_command": self.remediation_command,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "note": self.note,
        }
