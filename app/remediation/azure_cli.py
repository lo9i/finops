"""Azure CLI command generator. Commands are emitted only; operators run them."""
from __future__ import annotations

import shlex

from ..models import Resource


def _q(s: str | None) -> str:
    return shlex.quote(str(s)) if s is not None else "''"


def build_azure_command(action: str, resource: Resource) -> tuple[str, str]:
    """
    Returns (command, notes).

    Supported actions:
      - delete_disk
      - deallocate_vm
      - delete_snapshot
    """
    name = _q(resource.resource_id)
    rg = _q(resource.resource_group)

    missing_rg_note = (
        " NOTE: resource_group is not set on this resource; the command above contains an "
        "empty value — fill it in before running."
        if not resource.resource_group
        else ""
    )

    if action == "delete_disk":
        cmd = f"az disk delete --name {name} --resource-group {rg} --no-wait"
        notes = (
            "Azure CLI has no dry-run; review the disk and resource group before running. "
            "Pass `--yes` to skip the interactive confirmation." + missing_rg_note
        )
        return cmd, notes

    if action == "deallocate_vm":
        cmd = f"az vm deallocate --name {name} --resource-group {rg} --no-wait"
        notes = (
            "Deallocate stops compute billing (disks still charged). For permanent removal: "
            f"`az vm delete --name {name} --resource-group {rg} --yes`." + missing_rg_note
        )
        return cmd, notes

    if action == "delete_snapshot":
        cmd = f"az snapshot delete --name {name} --resource-group {rg}"
        notes = "Verify no images or disks are restored from this snapshot." + missing_rg_note
        return cmd, notes

    raise ValueError(f"Unknown Azure action: {action}")
