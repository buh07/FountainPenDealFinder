from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import DailyReportResponse
from ..services.reporting import generate_daily_report, get_daily_report, list_ranked_listings

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily/{report_date}", response_model=DailyReportResponse)
def get_daily(
    report_date: str,
    sort_by: Literal["risk_adjusted", "flat_profit", "percent_profit"] = Query(default="risk_adjusted"),
    db: Session = Depends(get_db),
) -> DailyReportResponse:
    try:
        parsed_date = date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD") from exc

    existing = get_daily_report(db, parsed_date)
    if existing is not None and sort_by == "risk_adjusted":
        return existing

    if existing is None:
        existing = generate_daily_report(db, parsed_date)

    if sort_by == "risk_adjusted":
        return existing

    generated_at = existing.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    generated_at = generated_at.astimezone(timezone.utc)
    if generated_at > datetime.now(timezone.utc):
        generated_at = datetime.now(timezone.utc)

    confident = list_ranked_listings(
        db,
        bucket="confident",
        sort_by=sort_by,
        limit=100,
        report_date=parsed_date,
        generated_at=generated_at,
    )
    potential = list_ranked_listings(
        db,
        bucket="potential",
        sort_by=sort_by,
        limit=100,
        report_date=parsed_date,
        generated_at=generated_at,
    )
    return DailyReportResponse(
        date=existing.date,
        generated_at=existing.generated_at,
        report_path=existing.report_path,
        confident=confident,
        potential=potential,
    )
