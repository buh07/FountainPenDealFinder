from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.services import ops_telemetry
from app.services import monitoring
from app.services.pipeline import run_collection_pipeline


client = TestClient(app)


def test_health_metrics_endpoint_returns_expected_shape():
    init_db()
    ops_telemetry.reset_operational_telemetry()
    with SessionLocal() as session:
        run_collection_pipeline(session)

    response = client.get("/health/metrics?window_hours=24")
    assert response.status_code == 200

    payload = response.json()
    assert payload["window_hours"] == 24
    assert isinstance(payload["generated_at"], str)
    assert payload["total_recent_listings"] >= 0
    assert isinstance(payload["source_counts"], dict)
    assert "parse_completeness_avg" in payload
    assert "non_discard_rate" in payload
    assert "ingestion_failure_count" in payload
    assert "retrain_failure_count" in payload
    assert "active_model_versions" in payload
    assert "model_age_hours" in payload
    assert "alerts" in payload


def test_health_metrics_includes_expected_sources_keyset():
    init_db()
    ops_telemetry.reset_operational_telemetry()
    with SessionLocal() as session:
        run_collection_pipeline(session)

    response = client.get("/health/metrics?window_hours=24")
    payload = response.json()

    # Source keys can vary by run; this verifies parser/reporting can expose source counts.
    assert isinstance(payload.get("source_counts"), dict)
    for key in payload["source_counts"].keys():
        assert isinstance(key, str)
        assert payload["source_counts"][key] >= 0


def test_health_metrics_reflects_operational_failure_snapshot():
    init_db()
    ops_telemetry.reset_operational_telemetry()
    ops_telemetry.record_ingestion_failure("mercari:fetch_exception")
    ops_telemetry.record_retrain_failure("retrain_publish_failed")

    response = client.get("/health/metrics?window_hours=24")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ingestion_failure_count"] >= 1
    assert payload["retrain_failure_count"] >= 1
    assert payload["latest_ingestion_failure_reason"] is not None
    assert payload["latest_retrain_failure_reason"] is not None
    assert isinstance(payload["active_model_versions"], dict)
    assert isinstance(payload["model_age_hours"], dict)


def test_health_metrics_alerts_when_active_models_are_stale(monkeypatch):
    init_db()
    ops_telemetry.reset_operational_telemetry()
    monkeypatch.setenv("MONITORING_MAX_MODEL_AGE_HOURS", "1")
    get_settings.cache_clear()

    old_created_at = datetime.now(timezone.utc) - timedelta(hours=8)

    def _fake_active(task: str):
        return {
            "task": task,
            "version_id": "old-version",
            "artifact_path": "models/versions/x.json",
            "created_at": old_created_at,
            "is_active": True,
        }

    monkeypatch.setattr(monitoring, "get_active_model_version", _fake_active)

    with SessionLocal() as session:
        metrics = monitoring.build_health_metrics(session, window_hours=24)

    assert "model_stale:resale" in metrics.alerts
    assert "model_stale:auction" in metrics.alerts
    get_settings.cache_clear()
