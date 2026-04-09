import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.model_registry import (
    promote_candidate_artifact,
    switch_active_to_version,
)
from app.services.pricing_models import clear_model_artifact_cache


client = TestClient(app)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_model_registry_env(monkeypatch, tmp_root: Path) -> None:
    monkeypatch.setenv("MODEL_VERSION_ROOT", str(tmp_root / "versions"))
    monkeypatch.setenv("MODEL_ACTIVE_POINTER_RESALE", str(tmp_root / "resale" / "active_pointer.txt"))
    monkeypatch.setenv("MODEL_ACTIVE_POINTER_AUCTION", str(tmp_root / "auction" / "active_pointer.txt"))
    monkeypatch.setenv("RESALE_MODEL_ARTIFACT_PATH", str(tmp_root / "resale" / "baseline_v1.json"))
    monkeypatch.setenv("AUCTION_MODEL_ARTIFACT_PATH", str(tmp_root / "auction" / "baseline_v1.json"))
    get_settings.cache_clear()
    clear_model_artifact_cache()


def test_model_version_endpoints_active_versions_and_rollback(monkeypatch):
    tmp_root = Path("/tmp/fpdf_test_model_registry_api")
    tmp_root.mkdir(parents=True, exist_ok=True)
    _configure_model_registry_env(monkeypatch, tmp_root)

    resale_candidate = tmp_root / "resale" / "baseline_v1.json"
    _write_json(resale_candidate, {"artifact": "resale", "value": 1})

    first = promote_candidate_artifact("resale", resale_candidate)
    switch_active_to_version("resale", str(first["version_id"]))

    _write_json(resale_candidate, {"artifact": "resale", "value": 2})
    second = promote_candidate_artifact("resale", resale_candidate)
    switch_active_to_version("resale", str(second["version_id"]))

    active_response = client.get("/retrain/models/resale/active")
    assert active_response.status_code == 200
    active_payload = active_response.json()
    assert active_payload["task"] == "resale"
    assert active_payload["active"] is not None
    assert active_payload["active"]["version_id"] == str(second["version_id"])

    versions_response = client.get("/retrain/models/resale/versions")
    assert versions_response.status_code == 200
    versions_payload = versions_response.json()
    assert versions_payload["task"] == "resale"
    assert len(versions_payload["versions"]) >= 2
    assert any(row["is_active"] for row in versions_payload["versions"])

    rollback_response = client.post(
        "/retrain/models/resale/rollback",
        json={"version_id": str(first["version_id"])},
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["task"] == "resale"
    assert rollback_payload["previous_version_id"] == str(second["version_id"])
    assert rollback_payload["active"]["version_id"] == str(first["version_id"])


def test_model_version_endpoints_reject_invalid_task():
    response = client.get("/retrain/models/invalid_task/active")
    assert response.status_code == 400
