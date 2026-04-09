from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import HealthAlertDispatchResponse, HealthMetricsResponse
from ..services.alerting import dispatch_health_alerts
from ..services.monitoring import build_health_metrics

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/metrics", response_model=HealthMetricsResponse)
def health_metrics(
    window_hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> HealthMetricsResponse:
    return build_health_metrics(db, window_hours=window_hours)


@router.post("/health/alerts/dispatch", response_model=HealthAlertDispatchResponse)
def dispatch_health_metrics_alerts(
    window_hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> HealthAlertDispatchResponse:
    metrics = build_health_metrics(db, window_hours=window_hours)
    return dispatch_health_alerts(db, metrics)
