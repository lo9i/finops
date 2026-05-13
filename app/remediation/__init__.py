"""Remediation: generate safe CLI commands. Never executes."""
from .aws_cli import build_aws_command
from .azure_cli import build_azure_command

__all__ = ["build_aws_command", "build_azure_command"]
