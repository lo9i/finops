"""AWS CLI v2 command generator. All destructive commands include --dry-run."""
from __future__ import annotations

import shlex

from ..models import Resource


def _q(s: str | None) -> str:
    """Quote a CLI argument safely."""
    return shlex.quote(str(s)) if s is not None else "''"


def build_aws_command(action: str, resource: Resource) -> tuple[str, str]:
    """
    Returns (command, notes).

    Supported actions:
      - delete_ebs_volume
      - stop_ec2_instance
      - delete_ebs_snapshot
      - delete_load_balancer
      - release_elastic_ip
    """
    rid = _q(resource.resource_id)
    region = _q(resource.region)

    if action == "delete_ebs_volume":
        cmd = (
            f"aws ec2 delete-volume --volume-id {rid} --region {region} --dry-run"
        )
        notes = (
            "Dry-run is on. Remove --dry-run to actually delete. "
            "Volume must be detached (state=available)."
        )
        return cmd, notes

    if action == "stop_ec2_instance":
        cmd = (
            f"aws ec2 stop-instances --instance-ids {rid} --region {region} --dry-run"
        )
        notes = (
            "Stops the instance (root EBS is preserved; you stop paying compute). "
            "For permanent removal, follow with `aws ec2 terminate-instances --instance-ids "
            f"{rid} --region {region}` after confirming the data is safe to lose."
        )
        return cmd, notes

    if action == "delete_ebs_snapshot":
        cmd = (
            f"aws ec2 delete-snapshot --snapshot-id {rid} --region {region} --dry-run"
        )
        notes = (
            "Confirm no AMI depends on this snapshot before deleting: "
            f"`aws ec2 describe-images --filters Name=block-device-mapping.snapshot-id,Values={rid} "
            f"--region {region}`."
        )
        return cmd, notes

    if action == "delete_load_balancer":
        # Resource ID is expected to be the ALB/NLB ARN for elbv2; v1 (classic) uses name.
        is_arn = str(resource.resource_id).startswith("arn:")
        if is_arn:
            cmd = (
                f"aws elbv2 delete-load-balancer --load-balancer-arn {rid} --region {region}"
            )
        else:
            cmd = (
                f"aws elb delete-load-balancer --load-balancer-name {rid} --region {region}"
            )
        notes = (
            "AWS LB delete has no --dry-run; review carefully. "
            "Drain connections first if production-adjacent."
        )
        return cmd, notes

    if action == "release_elastic_ip":
        alloc = None
        if resource.extra and isinstance(resource.extra, dict):
            alloc = resource.extra.get("allocation_id")
        target = _q(alloc) if alloc else rid
        cmd = (
            f"aws ec2 release-address --allocation-id {target} --region {region} --dry-run"
        )
        notes = "Releases an unattached Elastic IP. Free once detached; charged while idle."
        return cmd, notes

    raise ValueError(f"Unknown AWS action: {action}")
