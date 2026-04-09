import subprocess
import sys
from pathlib import Path

from ..core.config import get_settings
from .model_registry import fallback_artifact_path, promote_candidate_artifact, switch_active_to_version
from .ops_telemetry import record_retrain_failure
from .pricing_models import clear_model_artifact_cache


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def run_baseline_training_pipeline() -> tuple[str, str]:
    root = _repo_root()
    python_executable = sys.executable
    settings = get_settings()

    build_cmd = [python_executable, str(root / "scripts" / "build_historical_datasets.py")]
    train_cmd = [python_executable, str(root / "scripts" / "train_baseline_models.py")]
    eval_cmd = [
        python_executable,
        str(root / "scripts" / "evaluate_baseline_models.py"),
        "--min-rows",
        str(settings.baseline_eval_min_rows),
        "--resale-max-mape",
        str(settings.baseline_eval_resale_max_mape),
        "--auction-max-mape",
        str(settings.baseline_eval_auction_max_mape),
        "--report-path",
        settings.baseline_eval_report_path,
    ]

    build_proc = subprocess.run(
        build_cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    train_proc = subprocess.run(
        train_cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    eval_proc = subprocess.run(
        eval_cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )

    details = "\n".join(
        [
            "build_historical_datasets.py output:",
            (build_proc.stdout or "").strip(),
            (build_proc.stderr or "").strip(),
            "",
            "train_baseline_models.py output:",
            (train_proc.stdout or "").strip(),
            (train_proc.stderr or "").strip(),
            "",
            "evaluate_baseline_models.py output:",
            (eval_proc.stdout or "").strip(),
            (eval_proc.stderr or "").strip(),
        ]
    ).strip()

    if build_proc.returncode != 0 or train_proc.returncode != 0 or eval_proc.returncode != 0:
        record_retrain_failure("retrain_pipeline_command_failed")
        return "error", details

    promoted_versions: dict[str, str] = {}
    try:
        for task in ("resale", "auction"):
            candidate_path = fallback_artifact_path(task)  # baseline path produced by train script
            if not candidate_path.exists():
                raise FileNotFoundError(f"missing candidate artifact for task={task}: {candidate_path}")
            promoted = promote_candidate_artifact(task, candidate_path)
            promoted_versions[task] = str(promoted["version_id"])

        for task, version_id in promoted_versions.items():
            switch_active_to_version(task, version_id)
    except Exception as exc:
        record_retrain_failure(f"retrain_publish_failed:{exc.__class__.__name__}")
        return "error", f"{details}\n\nartifact_publish_error: {exc}"

    clear_model_artifact_cache()
    promotion_lines = "\n".join(
        [f"- {task}: {version_id}" for task, version_id in sorted(promoted_versions.items())]
    )
    details_with_versions = f"{details}\n\npromoted_versions:\n{promotion_lines}"
    return "ok", details_with_versions
