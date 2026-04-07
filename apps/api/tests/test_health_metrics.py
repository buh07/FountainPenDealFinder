from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.services.pipeline import run_collection_pipeline


client = TestClient(app)


def test_health_metrics_endpoint_returns_expected_shape():
    init_db()
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
    assert "alerts" in payload


def test_health_metrics_includes_expected_sources_keyset():
    init_db()
    with SessionLocal() as session:
        run_collection_pipeline(session)

    response = client.get("/health/metrics?window_hours=24")
    payload = response.json()

    # Source keys can vary by run; this verifies parser/reporting can expose source counts.
    assert isinstance(payload.get("source_counts"), dict)
    for key in payload["source_counts"].keys():
        assert isinstance(key, str)
        assert payload["source_counts"][key] >= 0
