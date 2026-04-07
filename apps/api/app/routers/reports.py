from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import DailyReportResponse
from ..services.reporting import generate_daily_report, get_daily_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily/{report_date}", response_model=DailyReportResponse)
def get_daily(report_date: str, db: Session = Depends(get_db)) -> DailyReportResponse:
    try:
        parsed_date = date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD") from exc

    existing = get_daily_report(db, parsed_date)
    if existing is not None:
        return existing

    return generate_daily_report(db, parsed_date)
