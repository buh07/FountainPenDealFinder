from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..core.config import get_settings


logger = logging.getLogger(__name__)


ModelTask = Literal["resale", "auction"]


@dataclass(frozen=True)
class _TaskConfig:
    fallback_attr: str
    pointer_attr: str


_TASKS: dict[str, _TaskConfig] = {
    "resale": _TaskConfig(
        fallback_attr="resale_model_artifact_path",
        pointer_attr="model_active_pointer_resale",
    ),
    "auction": _TaskConfig(
        fallback_attr="auction_model_artifact_path",
        pointer_attr="model_active_pointer_auction",
    ),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _as_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return _repo_root() / path


def _task_config(task: str) -> _TaskConfig:
    config = _TASKS.get(task)
    if config is None:
        raise ValueError(f"Unsupported model task: {task}")
    return config


def _task_directories(task: str) -> tuple[Path, Path, Path]:
    settings = get_settings()
    config = _task_config(task)
    fallback_path = _as_repo_path(str(getattr(settings, config.fallback_attr)))
    pointer_path = _as_repo_path(str(getattr(settings, config.pointer_attr)))
    versions_root = _as_repo_path(settings.model_version_root)
    task_versions_root = versions_root / task
    return fallback_path, pointer_path, task_versions_root


def _to_relative(path: Path) -> str:
    root = _repo_root()
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _read_pointer(pointer_path: Path) -> Path | None:
    if not pointer_path.exists():
        return None

    raw = pointer_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    pointer_value = Path(raw)
    if pointer_value.is_absolute():
        return pointer_value
    return _repo_root() / pointer_value


def resolve_active_artifact_path(task: ModelTask) -> Path:
    fallback_path, pointer_path, _ = _task_directories(task)
    pointed = _read_pointer(pointer_path)
    if pointed is not None and pointed.exists():
        return pointed
    return fallback_path


def fallback_artifact_path(task: ModelTask) -> Path:
    fallback_path, _, _ = _task_directories(task)
    return fallback_path


def _version_records(task: ModelTask) -> list[dict[str, str | bool | datetime]]:
    _fallback, _pointer, task_versions_root = _task_directories(task)
    if not task_versions_root.exists():
        return []

    rows: list[dict[str, str | bool | datetime]] = []
    for path in sorted(task_versions_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        rows.append(
            {
                "task": task,
                "version_id": path.stem,
                "artifact_path": _to_relative(path),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                "is_active": False,
            }
        )
    return rows


def list_model_versions(task: ModelTask) -> list[dict[str, str | bool | datetime]]:
    active_path = resolve_active_artifact_path(task).resolve()
    rows = _version_records(task)
    for row in rows:
        row_path = (_repo_root() / str(row["artifact_path"])).resolve() if not Path(str(row["artifact_path"])).is_absolute() else Path(str(row["artifact_path"])).resolve()
        if row_path == active_path:
            row["is_active"] = True
    return rows


def get_active_model_version(task: ModelTask) -> dict[str, str | bool | datetime] | None:
    active_path = resolve_active_artifact_path(task)
    versions = list_model_versions(task)
    for row in versions:
        if row.get("is_active"):
            return row

    if not active_path.exists():
        return None

    stat = active_path.stat()
    return {
        "task": task,
        "version_id": active_path.stem,
        "artifact_path": _to_relative(active_path),
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        "is_active": True,
    }


def _write_pointer(task: ModelTask, artifact_path: Path) -> None:
    _, pointer_path, _ = _task_directories(task)
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = pointer_path.with_suffix(pointer_path.suffix + ".tmp")
    tmp_path.write_text(_to_relative(artifact_path), encoding="utf-8")
    tmp_path.replace(pointer_path)


def promote_candidate_artifact(task: ModelTask, candidate_path: Path) -> dict[str, str | bool | datetime]:
    if not candidate_path.exists():
        raise FileNotFoundError(f"Candidate artifact missing for task={task}: {candidate_path}")

    # Validate JSON before publishing.
    json.loads(candidate_path.read_text(encoding="utf-8"))

    _, _, task_versions_root = _task_directories(task)
    task_versions_root.mkdir(parents=True, exist_ok=True)

    version_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_path = task_versions_root / f"{version_id}.json"
    suffix = 1
    while target_path.exists():
        suffix += 1
        target_path = task_versions_root / f"{version_id}_{suffix}.json"

    tmp_target = target_path.with_suffix(".json.tmp")
    tmp_target.write_text(candidate_path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_target.replace(target_path)

    stat = target_path.stat()
    return {
        "task": task,
        "version_id": target_path.stem,
        "artifact_path": _to_relative(target_path),
        "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        "is_active": False,
    }


def switch_active_to_version(task: ModelTask, version_id: str) -> dict[str, str | bool | datetime]:
    _, _, task_versions_root = _task_directories(task)
    target_path = task_versions_root / f"{version_id}.json"
    if not target_path.exists():
        raise FileNotFoundError(f"Model version not found for task={task}: {version_id}")

    _write_pointer(task, target_path)
    try:
        from .pricing_models import clear_model_artifact_cache

        clear_model_artifact_cache()
    except Exception:
        logger.warning(
            "Failed to clear model artifact cache after active version switch",
            extra={
                "task": task,
                "version_id": version_id,
            },
            exc_info=True,
        )
    record = get_active_model_version(task)
    if record is None:
        raise RuntimeError(f"Failed to resolve active version for task={task}")
    return record


def switch_active_to_artifact(task: ModelTask, artifact_path: Path) -> dict[str, str | bool | datetime]:
    if not artifact_path.exists():
        raise FileNotFoundError(f"Artifact path not found for task={task}: {artifact_path}")
    _write_pointer(task, artifact_path)
    try:
        from .pricing_models import clear_model_artifact_cache

        clear_model_artifact_cache()
    except Exception:
        logger.warning(
            "Failed to clear model artifact cache after active artifact switch",
            extra={
                "task": task,
                "artifact_path": str(artifact_path),
            },
            exc_info=True,
        )
    record = get_active_model_version(task)
    if record is None:
        raise RuntimeError(f"Failed to resolve active version for task={task}")
    return record


def active_version_id(task: ModelTask) -> str | None:
    active = get_active_model_version(task)
    if active is None:
        return None
    return str(active["version_id"])
