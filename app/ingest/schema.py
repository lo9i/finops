"""Normalized in-memory schema used between parsers and the DB layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class NormalizedBilling:
    provider: str            # "aws" | "azure"
    resource_id: Optional[str]
    service: str
    usage_start: Optional[datetime]
    usage_end: Optional[datetime]
    cost: float
    currency: str = "USD"
    region: Optional[str] = None
    usage_type: Optional[str] = None
    account_id: Optional[str] = None  # AWS UsageAccountId / Azure SubscriptionId
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedResource:
    resource_id: str
    provider: str
    resource_type: str
    region: str
    state: Optional[str] = None
    attachments: Optional[list[str]] = None
    last_activity_at: Optional[datetime] = None
    cpu_avg_7d: Optional[float] = None
    net_avg_7d: Optional[float] = None
    request_avg_7d: Optional[float] = None
    created_at: Optional[datetime] = None
    tags: Optional[dict[str, str]] = None
    resource_group: Optional[str] = None
    extra: Optional[dict[str, Any]] = None
    account_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = None
