"""Idle VM detectors: AWS EC2 + Azure VM."""
from __future__ import annotations

from typing import ClassVar, Iterable

from sqlalchemy.orm import Session

from .. import config
from ..models import Resource
from ..remediation import build_aws_command, build_azure_command
from .base import DetectorResult, RuleSpec, ThresholdSpec, estimate_monthly_cost


_CPU_THRESHOLD = ThresholdSpec(
    name="cpu_pct",
    description="Maximum 7-day average CPU utilisation (%) to consider an instance idle.",
    config_attr="IDLE_CPU_THRESHOLD_PCT",
    unit="%",
)
_NET_THRESHOLD = ThresholdSpec(
    name="net_bytes_per_day",
    description="Maximum 7-day daily-average network bytes to consider an instance idle.",
    config_attr="IDLE_NET_THRESHOLD_BYTES",
    unit="bytes/day",
)


class IdleEC2Detector:
    name = "idle_ec2"
    severity = "medium"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="idle_ec2",
        title="Idle EC2 Instance",
        description=(
            "EC2 instances in 'running' state with low CPU and low network activity "
            "over the past 7 days. These keep paying for compute while doing nothing."
        ),
        providers=("aws",),
        resource_types=("EC2_INSTANCE",),
        severity="medium",
        criteria=(
            "Provider is AWS",
            "Resource type is EC2_INSTANCE",
            "state == 'running'",
            "cpu_avg_7d < cpu_pct threshold",
            "net_avg_7d < net_bytes_per_day threshold (or null)",
        ),
        requires=("inventory",),
        thresholds=(_CPU_THRESHOLD, _NET_THRESHOLD),
        remediation_action="aws ec2 stop-instances (with --dry-run)",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "aws",
            Resource.resource_type == "EC2_INSTANCE",
        )
        for r in q:
            state = (r.state or "").lower()
            if state != "running":
                continue
            cpu = r.cpu_avg_7d
            net = r.net_avg_7d
            if cpu is None:
                continue
            if cpu < config.IDLE_CPU_THRESHOLD_PCT and (
                net is None or net < config.IDLE_NET_THRESHOLD_BYTES
            ):
                cost = estimate_monthly_cost(session, r.resource_id)
                cmd, notes = build_aws_command("stop_ec2_instance", r)
                yield DetectorResult(
                    resource=r,
                    detector=self.name,
                    severity=self.severity,
                    reason=(
                        f"EC2 instance {r.resource_id} is running but idle: "
                        f"CPU 7d avg={cpu:.2f}% (< {config.IDLE_CPU_THRESHOLD_PCT}%), "
                        f"net 7d avg={net if net is not None else 'n/a'} B/day."
                    ),
                    monthly_cost_estimate=cost,
                    remediation_command=cmd,
                    remediation_notes=notes,
                )


class IdleAzureVMDetector:
    name = "idle_azure_vm"
    severity = "medium"
    SPEC: ClassVar[RuleSpec] = RuleSpec(
        slug="idle_azure_vm",
        title="Idle Azure VM",
        description=(
            "Azure VMs reporting a running power state with low CPU utilisation "
            "over 7 days. Deallocate to stop paying compute (disks still incur cost)."
        ),
        providers=("azure",),
        resource_types=("AZURE_VM",),
        severity="medium",
        criteria=(
            "Provider is Azure",
            "Resource type is AZURE_VM",
            "state contains 'running' (e.g. 'PowerState/running')",
            "cpu_avg_7d < cpu_pct threshold",
        ),
        requires=("inventory",),
        thresholds=(_CPU_THRESHOLD,),
        remediation_action="az vm deallocate",
    )

    def find(self, session: Session) -> Iterable[DetectorResult]:
        q = session.query(Resource).filter(
            Resource.provider == "azure",
            Resource.resource_type == "AZURE_VM",
        )
        for r in q:
            state = (r.state or "").lower()
            if "running" not in state:
                continue
            cpu = r.cpu_avg_7d
            if cpu is None or cpu >= config.IDLE_CPU_THRESHOLD_PCT:
                continue
            cost = estimate_monthly_cost(session, r.resource_id)
            cmd, notes = build_azure_command("deallocate_vm", r)
            yield DetectorResult(
                resource=r,
                detector=self.name,
                severity=self.severity,
                reason=(
                    f"Azure VM {r.resource_id} is running but idle: "
                    f"CPU 7d avg={cpu:.2f}% (< {config.IDLE_CPU_THRESHOLD_PCT}%)."
                ),
                monthly_cost_estimate=cost,
                remediation_command=cmd,
                remediation_notes=notes,
            )
