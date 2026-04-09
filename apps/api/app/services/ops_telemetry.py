from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock


@dataclass
class _FailureCounter:
    count: int = 0
    latest_reason: str | None = None
    latest_at: datetime | None = None


_INGEST_FAILURE = _FailureCounter()
_RETRAIN_FAILURE = _FailureCounter()
_LOCK = Lock()


def _record(counter: _FailureCounter, reason: str) -> None:
    cleaned = (reason or "unknown_error").strip() or "unknown_error"
    with _LOCK:
        counter.count += 1
        counter.latest_reason = cleaned[:300]
        counter.latest_at = datetime.now(timezone.utc)


def record_ingestion_failure(reason: str) -> None:
    _record(_INGEST_FAILURE, reason)


def record_retrain_failure(reason: str) -> None:
    _record(_RETRAIN_FAILURE, reason)


def get_operational_failure_snapshot() -> dict[str, int | str | None]:
    with _LOCK:
        return {
            "ingestion_failure_count": _INGEST_FAILURE.count,
            "latest_ingestion_failure_reason": _INGEST_FAILURE.latest_reason,
            "retrain_failure_count": _RETRAIN_FAILURE.count,
            "latest_retrain_failure_reason": _RETRAIN_FAILURE.latest_reason,
        }


def reset_operational_telemetry() -> None:
    with _LOCK:
        _INGEST_FAILURE.count = 0
        _INGEST_FAILURE.latest_reason = None
        _INGEST_FAILURE.latest_at = None
        _RETRAIN_FAILURE.count = 0
        _RETRAIN_FAILURE.latest_reason = None
        _RETRAIN_FAILURE.latest_at = None
