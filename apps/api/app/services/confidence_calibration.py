from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import ClassificationResult, ManualReview


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _label_from_review(review: ManualReview) -> int | None:
    if review.is_false_positive:
        return 0

    positive_actions = {"confirm_classification"}
    negative_actions = {
        "correct_classification",
        "add_new_type",
        "mark_fake_suspicious",
        "mark_condition_worse",
        "mark_not_worth_it",
    }

    action = str(review.action_type or "").strip()
    if action in positive_actions:
        return 1
    if action in negative_actions:
        return 0
    return None


@dataclass(frozen=True)
class _Calibrator:
    applied: bool
    sample_count: int
    min_rows: int
    bin_count: int
    bin_upper_bounds: tuple[float, ...] = ()
    calibrated_values: tuple[float, ...] = ()

    def calibrate(self, raw_confidence: float) -> float:
        raw = _clip01(raw_confidence)
        if not self.applied or not self.bin_upper_bounds:
            return raw

        for idx, upper in enumerate(self.bin_upper_bounds):
            if raw <= upper:
                return _clip01(self.calibrated_values[idx])
        return _clip01(self.calibrated_values[-1])

    def info(self, raw_confidence: float, calibrated_confidence: float) -> dict[str, Any]:
        return {
            "method": "monotonic_binned_isotonic",
            "applied": self.applied,
            "sample_count": self.sample_count,
            "min_rows": self.min_rows,
            "bin_count": self.bin_count,
            "raw_confidence": round(_clip01(raw_confidence), 4),
            "calibrated_confidence": round(_clip01(calibrated_confidence), 4),
        }


_CACHE_LOCK = Lock()
_CACHE_KEY: tuple[int, str | None] | None = None
_CACHE_CALIBRATOR: _Calibrator | None = None


def _latest_review_fingerprint(session: Session) -> tuple[int, str | None]:
    count, latest = session.execute(
        select(
            func.count(ManualReview.review_id),
            func.max(ManualReview.created_at),
        )
    ).one()
    latest_value: str | None = None
    if isinstance(latest, datetime):
        latest_value = latest.isoformat()
    return int(count or 0), latest_value


def _latest_labeled_reviews_by_listing(session: Session) -> dict[str, int]:
    reviews = session.scalars(
        select(ManualReview).order_by(ManualReview.created_at.desc())
    ).all()

    by_listing: dict[str, int] = {}
    for review in reviews:
        if review.listing_id in by_listing:
            continue
        label = _label_from_review(review)
        if label is None:
            continue
        by_listing[review.listing_id] = int(label)
    return by_listing


def _build_monotonic_binned_calibrator(
    samples: list[tuple[float, int]],
    *,
    min_rows: int,
    bin_count: int,
) -> _Calibrator:
    if len(samples) < min_rows:
        return _Calibrator(
            applied=False,
            sample_count=len(samples),
            min_rows=min_rows,
            bin_count=bin_count,
        )

    ordered = sorted(samples, key=lambda item: item[0])
    bucket_count = max(2, min(bin_count, len(ordered)))

    bins: list[dict[str, Any]] = []
    for index in range(bucket_count):
        start = (index * len(ordered)) // bucket_count
        end = ((index + 1) * len(ordered)) // bucket_count
        chunk = ordered[start:end]
        if not chunk:
            continue
        count = len(chunk)
        avg_label = sum(label for _confidence, label in chunk) / count
        upper = float(chunk[-1][0])
        bins.append(
            {
                "index": len(bins),
                "count": count,
                "avg_label": float(avg_label),
                "upper": upper,
            }
        )

    if len(bins) < 2:
        return _Calibrator(
            applied=False,
            sample_count=len(samples),
            min_rows=min_rows,
            bin_count=bin_count,
        )

    blocks: list[dict[str, Any]] = []
    for idx, bucket in enumerate(bins):
        blocks.append(
            {
                "start": idx,
                "end": idx,
                "weight": int(bucket["count"]),
                "value": float(bucket["avg_label"]),
            }
        )
        while len(blocks) >= 2 and blocks[-2]["value"] > blocks[-1]["value"]:
            right = blocks.pop()
            left = blocks.pop()
            merged_weight = int(left["weight"]) + int(right["weight"])
            merged_value = 0.0
            if merged_weight > 0:
                merged_value = (
                    (float(left["value"]) * int(left["weight"]))
                    + (float(right["value"]) * int(right["weight"]))
                ) / merged_weight
            blocks.append(
                {
                    "start": int(left["start"]),
                    "end": int(right["end"]),
                    "weight": merged_weight,
                    "value": float(merged_value),
                }
            )

    calibrated_by_bucket = [0.0] * len(bins)
    for block in blocks:
        for idx in range(int(block["start"]), int(block["end"]) + 1):
            calibrated_by_bucket[idx] = _clip01(float(block["value"]))

    uppers = tuple(float(bucket["upper"]) for bucket in bins)
    calibrated_values = tuple(float(value) for value in calibrated_by_bucket)
    return _Calibrator(
        applied=True,
        sample_count=len(samples),
        min_rows=min_rows,
        bin_count=bin_count,
        bin_upper_bounds=uppers,
        calibrated_values=calibrated_values,
    )


def _build_calibrator(session: Session) -> _Calibrator:
    settings = get_settings()
    labeled_by_listing = _latest_labeled_reviews_by_listing(session)
    if not labeled_by_listing:
        return _Calibrator(
            applied=False,
            sample_count=0,
            min_rows=max(5, int(settings.classification_calibration_min_rows)),
            bin_count=max(2, int(settings.classification_calibration_bin_count)),
        )

    rows = session.scalars(
        select(ClassificationResult).where(
            ClassificationResult.listing_id.in_(list(labeled_by_listing.keys()))
        )
    ).all()

    samples: list[tuple[float, int]] = []
    for row in rows:
        label = labeled_by_listing.get(row.listing_id)
        if label is None:
            continue
        samples.append((_clip01(float(row.classification_confidence or 0.0)), int(label)))

    return _build_monotonic_binned_calibrator(
        samples,
        min_rows=max(5, int(settings.classification_calibration_min_rows)),
        bin_count=max(2, int(settings.classification_calibration_bin_count)),
    )


def _cached_calibrator(session: Session) -> _Calibrator:
    global _CACHE_KEY, _CACHE_CALIBRATOR

    cache_key = _latest_review_fingerprint(session)
    with _CACHE_LOCK:
        if _CACHE_CALIBRATOR is not None and _CACHE_KEY == cache_key:
            return _CACHE_CALIBRATOR

    calibrator = _build_calibrator(session)
    with _CACHE_LOCK:
        _CACHE_KEY = cache_key
        _CACHE_CALIBRATOR = calibrator
    return calibrator


def calibrate_classification_confidence(
    session: Session,
    raw_confidence: float,
) -> tuple[float, dict[str, Any]]:
    raw = _clip01(raw_confidence)
    calibrator = _cached_calibrator(session)
    calibrated = calibrator.calibrate(raw)
    return calibrated, calibrator.info(raw, calibrated)


def reset_confidence_calibration_cache() -> None:
    global _CACHE_KEY, _CACHE_CALIBRATOR
    with _CACHE_LOCK:
        _CACHE_KEY = None
        _CACHE_CALIBRATOR = None
