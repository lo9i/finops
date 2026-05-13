"""Background ingestion: stream upload to a temp file, parse it off the request thread."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import UploadFile

from ..db import session_scope
from ..detectors import run_all
from ..models import Ingestion
from .loader import ingest_billing_file, ingest_inventory_file

log = logging.getLogger("ingest.stream")


def stream_upload_to_temp(upload: UploadFile, suffix: str = "") -> tuple[Path, int]:
    """Copy the multipart upload to a NamedTemporaryFile without buffering it in memory.

    Starlette's `UploadFile.file` is a SpooledTemporaryFile; `shutil.copyfileobj` walks it
    in 64 KiB chunks, so the source is never fully materialised in RAM.

    Returns (path, size_bytes). Caller is responsible for unlinking the file when done.
    """
    if not suffix and upload.filename:
        suffix = Path(upload.filename).suffix
    tmp = tempfile.NamedTemporaryFile(prefix="cost-opt-", suffix=suffix, delete=False)
    try:
        shutil.copyfileobj(upload.file, tmp, length=64 * 1024)
    finally:
        tmp.close()
    return Path(tmp.name), os.path.getsize(tmp.name)


def create_queued_ingestion(
    kind: Literal["billing", "inventory"],
    filename: str,
    size_bytes: int,
) -> int:
    """Insert a placeholder Ingestion row and return its id."""
    with session_scope() as session:
        ing = Ingestion(
            filename=filename,
            kind=kind,
            size_bytes=size_bytes,
            warnings=[],
            processing_state="queued",
            rows_processed=0,
        )
        session.add(ing)
        session.flush()
        return ing.id


def process_pending_ingestion(
    ingestion_id: int,
    temp_path: str,
    kind: Literal["billing", "inventory"],
    filename: str,
) -> None:
    """Background worker — read the temp file, parse, run detectors, update state."""
    path = Path(temp_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        log.exception("could not read temp file %s", temp_path)
        with session_scope() as session:
            ing = session.get(Ingestion, ingestion_id)
            if ing:
                ing.status = "failed"
                ing.processing_state = "done"
                ing.error_message = f"failed to read upload: {exc}"
        return

    try:
        with session_scope() as session:
            ing = session.get(Ingestion, ingestion_id)
            if ing is None:
                log.error("ingestion %s vanished before processing", ingestion_id)
                return
            ing.processing_state = "processing"
            session.flush()

            if kind == "billing":
                ingest_billing_file(session, filename, content, ingestion_id=ingestion_id)
            else:
                ingest_inventory_file(session, filename, content, ingestion_id=ingestion_id)

            session.refresh(ing)
            if ing.status != "failed":
                run_all(session, ingestion_id=ing.id, trigger="ingest")

            ing.rows_processed = ing.rows_ingested
            ing.processing_state = "done"
    except Exception as exc:  # pragma: no cover — best-effort safety net
        log.exception("background ingest failed for %s", filename)
        with session_scope() as session:
            ing = session.get(Ingestion, ingestion_id)
            if ing:
                ing.status = "failed"
                ing.error_message = str(exc)
                ing.processing_state = "done"

    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass
