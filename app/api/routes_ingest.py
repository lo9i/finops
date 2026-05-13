"""Ingestion endpoints — stream the upload to disk, process in the background."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_session
from ..ingest.stream import (
    create_queued_ingestion,
    process_pending_ingestion,
    stream_upload_to_temp,
)
from ..models import Ingestion

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


async def _ingest_async(
    kind: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    session: Session,
):
    filename = file.filename or kind
    try:
        temp_path, size = stream_upload_to_temp(file)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"upload streaming failed: {exc}") from exc

    ingestion_id = create_queued_ingestion(kind=kind, filename=filename, size_bytes=size)

    # Hand off to the background queue. With FastAPI's BackgroundTasks the function
    # runs after the response is sent. For really large workloads, swap this layer for
    # arq / RQ / Celery — the interface stays the same.
    background_tasks.add_task(
        process_pending_ingestion,
        ingestion_id=ingestion_id,
        temp_path=str(temp_path),
        kind=kind,
        filename=filename,
    )

    ing = session.get(Ingestion, ingestion_id)
    payload = ing.to_dict() if ing else {"id": ingestion_id, "processing_state": "queued"}
    payload["queued"] = True
    return payload


@router.post("/billing")
async def ingest_billing(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    return await _ingest_async("billing", file, background_tasks, session)


@router.post("/inventory")
async def ingest_inventory(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    return await _ingest_async("inventory", file, background_tasks, session)
