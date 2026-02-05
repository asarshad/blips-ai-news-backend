from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.ingestion.time import get_ingestion_day
from app.quality.report_service import build_quality_report
from app.schemas.quality import QualityReport

router = APIRouter()


@router.get("/report", response_model=QualityReport)
def get_quality_report(
    *,
    db: Session = Depends(get_db),  # noqa: B008
    day: date | None = Query(  # noqa: B008
        default=None,
        description="Ingestion day (YYYY-MM-DD). Defaults to ingestion-day timezone.",
    ),
):
    report_day = day or get_ingestion_day()
    return build_quality_report(db, day=report_day)
