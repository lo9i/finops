"""Tool definitions exposed to Claude. All read-only; no destructive operations."""
from __future__ import annotations

import json
from typing import Optional

from anthropic import beta_tool

from ..db import session_scope
from ..detectors import list_rules as _list_rules
from ..api.routes_findings import build_summary, query_findings
from ..api.routes_resources import (
    get_resource as _get_resource,
    list_resources as _list_resources,
)


SYSTEM_PROMPT = """You are the FinOps assistant for a cloud cost optimizer.

Today is 2026-05-13. The operator manages AWS and Azure resources and has
ingested billing exports plus inventory snapshots into the engine.

You have READ-ONLY tools that query the engine's database. Use them to answer.
Cite specific resource IDs, dollar amounts, and detector slugs. Prefer bullet
points for lists. Keep responses to 1-3 sentences unless the question requires
detail.

Important constraints:
- You CANNOT execute remediation commands or modify any state. If the user asks
  you to delete or modify a resource, explain that the engine only generates
  commands and direct them to the "Mark released" button in the dashboard.
- All amounts you cite must come from a tool call. Never invent figures.
- If a user query is ambiguous, ask one clarifying question before calling tools.
"""


def _serialize(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


@beta_tool
def list_resources(
    provider: str = "",
    resource_type: str = "",
    account_id: str = "",
    status: str = "",
    search: str = "",
    limit: int = 20,
) -> str:
    """Search the cloud resource inventory.

    Use this to answer questions like 'what AWS volumes are unattached?',
    'show me idle resources in account X', or 'find resources whose ID
    contains foo'. Returns the top matches sorted by total billed cost.

    Args:
        provider: 'aws' or 'azure'. Empty string means any.
        resource_type: Specific type like 'EBS_VOLUME', 'EC2_INSTANCE',
            'AZURE_DISK', 'AZURE_VM', 'EBS_SNAPSHOT', 'ALB', 'ELASTIC_IP'.
        account_id: AWS account ID (12 digits) or Azure subscription GUID.
        status: 'open' (has unfixed findings), 'clean' (no issues),
            'released' (already remediated). Empty = all statuses.
        search: Case-insensitive substring match on resource_id.
        limit: Cap on resources returned (default 20, max 100).
    """
    cap = max(1, min(limit, 100))
    with session_scope() as session:
        data = _list_resources(
            provider=provider or None,
            resource_type=resource_type or None,
            account_id=account_id or None,
            is_inferred=None,
            include_released=True,
            status=status or None,
            search=search or None,
            sort="total_cost",
            order="desc",
            session=session,
        )
    slim = [
        {
            "resource_id": r["resource_id"],
            "provider": r["provider"],
            "type": r["resource_type"],
            "region": r["region"],
            "account_id": r["account_id"],
            "state": r["state"],
            "total_cost": r["total_cost"],
            "open_findings_count": r["open_findings_count"],
            "released_count": r["released_count"],
            "is_inferred": r["is_inferred"],
            "first_seen_at": r["first_seen_at"],
            "last_seen_at": r["last_seen_at"],
        }
        for r in data["resources"][:cap]
    ]
    return _serialize({
        "count": data["count"],
        "showing": len(slim),
        "resources": slim,
    })


@beta_tool
def list_findings(
    provider: str = "",
    severity: str = "",
    detector: str = "",
    limit: int = 20,
) -> str:
    """List open findings (waste detections that haven't been released yet).

    Use this for questions like 'what's my biggest waste?', 'show all
    high-severity findings', or 'what does the idle_ec2 detector see right
    now?'.

    Args:
        provider: 'aws' or 'azure'. Empty means any.
        severity: 'low', 'medium', 'high'. Empty means any.
        detector: Specific detector slug like 'orphan_ebs_volume',
            'idle_ec2', 'unmonitored_long_running'. Use the 'list_rules'
            tool to see all detector slugs.
        limit: Cap on findings returned (default 20, max 100).
    """
    cap = max(1, min(limit, 100))
    with session_scope() as session:
        findings = query_findings(
            session,
            provider=provider or None,
            severity=severity or None,
            detector=detector or None,
        )
    slim = [
        {
            "id": f["id"],
            "severity": f["severity"],
            "detector": f["detector"],
            "monthly_cost_estimate": f["monthly_cost_estimate"],
            "resource": f["resource"],
            "reason": f["reason"],
            "remediation_command": f["remediation_command"],
        }
        for f in findings[:cap]
    ]
    return _serialize({
        "total": len(findings),
        "showing": len(slim),
        "findings": slim,
    })


@beta_tool
def get_summary() -> str:
    """Get the dashboard summary: total waste, savings, detector breakdown,
    provider/account breakdowns. Use for 'how much am I wasting?' type questions.
    """
    with session_scope() as session:
        return _serialize(build_summary(session))


@beta_tool
def list_rules() -> str:
    """List every detection rule (criteria, severity, providers, thresholds).
    Use when the user asks about how detection works or which rules exist.
    """
    return _serialize([r.to_dict() for r in _list_rules()])


@beta_tool
def get_resource(resource_id: str) -> str:
    """Get full detail for one resource including findings, release history,
    total cost, and ingestion provenance. Use when the user asks about a
    specific resource by ID.
    """
    with session_scope() as session:
        try:
            return _serialize(_get_resource(resource_id=resource_id, session=session))
        except Exception as exc:
            return _serialize({"error": str(exc)})


ALL_TOOLS = [list_resources, list_findings, get_summary, list_rules, get_resource]
