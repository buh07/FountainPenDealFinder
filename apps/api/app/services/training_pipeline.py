import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def run_baseline_training_pipeline() -> tuple[str, str]:
    root = _repo_root()
    python_executable = sys.executable

    build_cmd = [python_executable, str(root / "scripts" / "build_historical_datasets.py")]
    train_cmd = [python_executable, str(root / "scripts" / "train_baseline_models.py")]

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

    details = "\n".join(
        [
            "build_historical_datasets.py output:",
            (build_proc.stdout or "").strip(),
            (build_proc.stderr or "").strip(),
            "",
            "train_baseline_models.py output:",
            (train_proc.stdout or "").strip(),
            (train_proc.stderr or "").strip(),
        ]
    ).strip()

    if build_proc.returncode != 0 or train_proc.returncode != 0:
        return "error", details

    return "ok", details
