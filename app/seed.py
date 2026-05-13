"""Seed demo data from the bundled samples/ directory.

Drops and recreates all tables, then ingests the bundled sample files in order
so the Ingestions page shows realistic history.
"""
from __future__ import annotations

from . import config
from .db import Base, engine, session_scope
from .ingest import ingest_billing_file, ingest_inventory_file


SAMPLE_FILES = [
    # Older billing first — establishes first_seen_at far in the past for some resources.
    ("billing", "aws_cur_history_sample.csv"),
    ("billing", "aws_cur_sample.csv"),
    ("billing", "azure_export_sample.json"),
    ("inventory", "aws_inventory_sample.json"),
    ("inventory", "azure_inventory_sample.json"),
    # Final upload demonstrates per-row warnings.
    ("inventory", "inventory_with_warnings_sample.json"),
]


def main() -> None:
    # Wipe schema + recreate (cheap; this is demo data).
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    samples = config.SAMPLES_DIR
    summary: list[str] = []

    for kind, fname in SAMPLE_FILES:
        path = samples / fname
        content = path.read_bytes()
        with session_scope() as s:
            if kind == "billing":
                ing = ingest_billing_file(s, fname, content)
            else:
                ing = ingest_inventory_file(s, fname, content)
            # Auto-trigger detector pass after each ingest (matches the API behavior).
            from .detectors import run_all

            if ing.status != "failed":
                run_all(s, ingestion_id=ing.id, trigger="ingest")
            summary.append(
                f"  · {fname}: status={ing.status} rows={ing.rows_ingested}/{ing.rows_total} warnings={len(ing.warnings or [])}"
            )

    print("[seed] Ingested sample files:")
    for line in summary:
        print(line)


def cli() -> None:
    """Entrypoint for the cost-optimizer-seed script."""
    main()


if __name__ == "__main__":
    main()
