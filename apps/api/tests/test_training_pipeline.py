import json
from pathlib import Path
from types import SimpleNamespace

from app.services import ops_telemetry
from app.services import training_pipeline


class _Proc(SimpleNamespace):
    returncode: int = 0
    stdout: str = "ok"
    stderr: str = ""


def test_run_baseline_training_pipeline_clears_model_cache_on_success(monkeypatch):
    monkeypatch.setattr(training_pipeline.subprocess, "run", lambda *args, **kwargs: _Proc())
    called = {"count": 0}

    def _clear():
        called["count"] += 1

    monkeypatch.setattr(training_pipeline, "clear_model_artifact_cache", _clear)
    monkeypatch.setattr(training_pipeline, "switch_active_to_version", lambda task, version_id: None)

    tmp_dir = Path("/tmp/fpdf_test_model_versions")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "resale.json").write_text(json.dumps({"artifact": "resale"}), encoding="utf-8")
    (tmp_dir / "auction.json").write_text(json.dumps({"artifact": "auction"}), encoding="utf-8")

    def _fallback(task: str) -> Path:
        return tmp_dir / ("resale.json" if task == "resale" else "auction.json")

    monkeypatch.setattr(training_pipeline, "fallback_artifact_path", _fallback)
    monkeypatch.setattr(
        training_pipeline,
        "promote_candidate_artifact",
        lambda task, candidate_path: {
            "task": task,
            "version_id": f"{task}_v2",
            "artifact_path": str(candidate_path),
        },
    )

    status, _details = training_pipeline.run_baseline_training_pipeline()
    assert status == "ok"
    assert called["count"] == 1


def test_run_baseline_training_pipeline_skips_cache_clear_on_failure(monkeypatch):
    procs = [_Proc(returncode=1), _Proc(returncode=0), _Proc(returncode=0)]

    def _fake_run(*args, **kwargs):
        return procs.pop(0)

    monkeypatch.setattr(training_pipeline.subprocess, "run", _fake_run)
    called = {"count": 0}

    def _clear():
        called["count"] += 1

    monkeypatch.setattr(training_pipeline, "clear_model_artifact_cache", _clear)
    monkeypatch.setattr(training_pipeline, "switch_active_to_version", lambda task, version_id: None)
    monkeypatch.setattr(training_pipeline, "fallback_artifact_path", lambda task: Path("/tmp/none.json"))
    monkeypatch.setattr(
        training_pipeline,
        "promote_candidate_artifact",
        lambda task, candidate_path: {"task": task, "version_id": "x"},
    )

    status, _details = training_pipeline.run_baseline_training_pipeline()
    assert status == "error"
    assert called["count"] == 0


def test_run_baseline_training_pipeline_records_retrain_failure_on_publish_error(monkeypatch):
    monkeypatch.setattr(training_pipeline.subprocess, "run", lambda *args, **kwargs: _Proc())
    monkeypatch.setattr(training_pipeline, "clear_model_artifact_cache", lambda: None)

    tmp_dir = Path("/tmp/fpdf_test_model_versions_fail")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "resale.json").write_text(json.dumps({"artifact": "resale"}), encoding="utf-8")
    (tmp_dir / "auction.json").write_text(json.dumps({"artifact": "auction"}), encoding="utf-8")

    monkeypatch.setattr(
        training_pipeline,
        "fallback_artifact_path",
        lambda task: tmp_dir / ("resale.json" if task == "resale" else "auction.json"),
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("publish failed")

    monkeypatch.setattr(training_pipeline, "promote_candidate_artifact", _raise)
    ops_telemetry.reset_operational_telemetry()

    status, _details = training_pipeline.run_baseline_training_pipeline()
    snapshot = ops_telemetry.get_operational_failure_snapshot()

    assert status == "error"
    assert int(snapshot["retrain_failure_count"] or 0) >= 1
    assert snapshot["latest_retrain_failure_reason"] is not None
