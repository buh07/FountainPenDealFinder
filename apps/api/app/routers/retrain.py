from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from ..schemas import (
    ActiveModelVersionResponse,
    ModelRollbackRequest,
    ModelRollbackResponse,
    ModelVersionInfo,
    ModelVersionListResponse,
    RetrainJobResponse,
)
from ..services.model_registry import (
    active_version_id,
    fallback_artifact_path,
    get_active_model_version,
    list_model_versions,
    switch_active_to_version,
)
from ..services.pricing_models import clear_model_artifact_cache
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


def _validate_task(task: str) -> str:
    normalized = task.strip().lower()
    if normalized not in {"resale", "auction"}:
        raise HTTPException(status_code=400, detail="task must be one of: resale, auction")
    return normalized


@router.get("/models/{task}/active", response_model=ActiveModelVersionResponse)
def get_active_model(task: str) -> ActiveModelVersionResponse:
    model_task = _validate_task(task)
    active = get_active_model_version(model_task)
    active_payload = ModelVersionInfo(**active) if isinstance(active, dict) else None
    return ActiveModelVersionResponse(
        task=model_task,
        active=active_payload,
        fallback_artifact_path=str(fallback_artifact_path(model_task)),
    )


@router.get("/models/{task}/versions", response_model=ModelVersionListResponse)
def get_model_versions(task: str) -> ModelVersionListResponse:
    model_task = _validate_task(task)
    versions = [ModelVersionInfo(**row) for row in list_model_versions(model_task)]
    return ModelVersionListResponse(
        task=model_task,
        active_version_id=active_version_id(model_task),
        versions=versions,
    )


@router.post("/models/{task}/rollback", response_model=ModelRollbackResponse)
def rollback_model(task: str, payload: ModelRollbackRequest) -> ModelRollbackResponse:
    model_task = _validate_task(task)
    previous = active_version_id(model_task)
    try:
        active = switch_active_to_version(model_task, payload.version_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    clear_model_artifact_cache()
    return ModelRollbackResponse(
        task=model_task,
        previous_version_id=previous,
        active=ModelVersionInfo(**active),
    )
