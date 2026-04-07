from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import CollectRunRequest, CollectRunResponse
from ..services.pipeline import run_collection_pipeline

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
        report_path=result.get("report_path"),
    )
