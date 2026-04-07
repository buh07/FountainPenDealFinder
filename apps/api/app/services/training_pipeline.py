import subprocess
import sys
from pathlib import Path

from ..core.config import get_settings


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
        return "error", details

    return "ok", details
