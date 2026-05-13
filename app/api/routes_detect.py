"""Manual detection trigger."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..detectors import run_all

router = APIRouter(prefix="/api/detect", tags=["detect"])


@router.post("/run")
def detect_run(session: Session = Depends(get_session)):
    run = run_all(session, trigger="manual")
    session.commit()
    return run.to_dict()
