from datetime import datetime, timezone

from fastapi import APIRouter

from ..schemas import RetrainJobResponse
from ..services.training_pipeline import run_baseline_training_pipeline

router = APIRouter(prefix="/retrain", tags=["retrain"])


@router.post("/jobs", response_model=RetrainJobResponse)
def run_retrain_job() -> RetrainJobResponse:
    started_at = datetime.now(timezone.utc)
    status, details = run_baseline_training_pipeline()
    finished_at = datetime.now(timezone.utc)
    return RetrainJobResponse(
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        details=details,
    )
