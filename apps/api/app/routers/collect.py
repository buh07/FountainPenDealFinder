from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import (
    CollectRunRequest,
    CollectRunResponse,
    EndingAuctionRefreshResponse,
    PriorityAuctionRefreshResponse,
)
from ..services.pipeline import (
    run_collection_pipeline,
    run_ending_auction_refresh,
    run_priority_auction_refresh,
)

router = APIRouter(prefix="/collect", tags=["collect"])


@router.post("/run", response_model=CollectRunResponse)
def run_collect(
    payload: CollectRunRequest | None = None,
    db: Session = Depends(get_db),
) -> CollectRunResponse:
    started_at = datetime.now(timezone.utc)
    result = run_collection_pipeline(db, payload.report_date if payload else None)
    finished_at = datetime.now(timezone.utc)
    return CollectRunResponse(
        started_at=started_at,
        finished_at=finished_at,
        ingested_count=result["ingested_count"],
        scored_count=result["scored_count"],
        confident_count=result["confident_count"],
        potential_count=result["potential_count"],
        source_counts=result.get("source_counts", {}),
        report_path=result.get("report_path"),
    )


@router.post("/refresh-ending", response_model=EndingAuctionRefreshResponse)
def refresh_ending_auctions(
    window_hours: int = Query(default=24, ge=1, le=72),
    db: Session = Depends(get_db),
) -> EndingAuctionRefreshResponse:
    started_at = datetime.now(timezone.utc)
    result = run_ending_auction_refresh(db, window_hours=window_hours)
    finished_at = datetime.now(timezone.utc)
    return EndingAuctionRefreshResponse(
        started_at=started_at,
        finished_at=finished_at,
        ingested_count=result["ingested_count"],
        scored_count=result["scored_count"],
        window_hours=result["window_hours"],
    )


@router.post("/refresh-priority", response_model=PriorityAuctionRefreshResponse)
def refresh_priority_auctions(
    window_hours: int = Query(default=2, ge=1, le=24),
    threshold: float = Query(default=0.55, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> PriorityAuctionRefreshResponse:
    started_at = datetime.now(timezone.utc)
    result = run_priority_auction_refresh(
        db,
        window_hours=window_hours,
        threshold=threshold,
    )
    finished_at = datetime.now(timezone.utc)
    return PriorityAuctionRefreshResponse(
        started_at=started_at,
        finished_at=finished_at,
        candidate_count=result["candidate_count"],
        ingested_count=result["ingested_count"],
        scored_count=result["scored_count"],
        window_hours=result["window_hours"],
        threshold=result["threshold"],
    )
