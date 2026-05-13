"""Database reset — drops and recreates every table.

Usage:
  uv run python -m app.reset            # interactive: prompts for confirmation
  uv run python -m app.reset --yes      # non-interactive (for scripts / CI)
  uv run cost-optimizer-reset           # same, via project.scripts entry point

This wipes the cost-optimizer SQLite database back to an empty schema. No demo
seed data is loaded — for a populated DB, follow with `uv run python -m app.seed`.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func

from . import config
from .db import Base, SessionLocal, engine
from .models import (
    BillingRecord,
    DetectionRun,
    Finding,
    Ingestion,
    ReleasedResource,
    Resource,
)


def _row_counts() -> dict[str, int]:
    """Snapshot the DB contents before we wipe, so the user sees what's deleted."""
    counts: dict[str, int] = {}
    with SessionLocal() as s:
        for model in (
            Ingestion,
            BillingRecord,
            Resource,
            DetectionRun,
            Finding,
            ReleasedResource,
        ):
            try:
                counts[model.__tablename__] = (
                    s.query(func.count()).select_from(model).scalar() or 0
                )
            except Exception:
                # Table might not exist yet — first-ever reset.
                counts[model.__tablename__] = 0
    return counts


def reset(*, confirm: bool = True) -> None:
    counts = _row_counts()
    total = sum(counts.values())

    print(f"Database: {config.DB_URL}")
    if total == 0:
        print("Database is already empty — schema will be recreated for safety.")
    else:
        print("Current contents:")
        for name, n in counts.items():
            print(f"  {name:<22} {n} row(s)")

    if confirm:
        try:
            answer = input("\nDrop ALL tables and recreate schema? Type 'yes' to continue: ").strip().lower()
        except EOFError:
            print("\nNo TTY — re-run with --yes to confirm non-interactively.")
            sys.exit(2)
        if answer != "yes":
            print("Aborted.")
            sys.exit(1)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print(f"[reset] Cleared. {config.DB_URL} now contains empty tables only.")
    print("[reset] To repopulate with sample data, run: uv run python -m app.seed")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="app.reset",
        description="Drop and recreate the cost-optimizer database tables.",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip the confirmation prompt (for scripts / CI).",
    )
    args = parser.parse_args()
    reset(confirm=not args.yes)


if __name__ == "__main__":
    main()
