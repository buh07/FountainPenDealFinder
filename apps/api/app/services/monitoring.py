import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import DealScore, ManualReview, RawListing
from ..schemas import HealthMetricsResponse
from .model_registry import get_active_model_version
from .ops_telemetry import get_operational_failure_snapshot


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parse_completeness(listing: RawListing) -> float:
    fields = [
        bool(listing.source_listing_id),
        bool(listing.url),
        bool(listing.title),
        bool(listing.listing_format),
        bool((listing.current_price_jpy or 0) > 0 or (listing.price_buy_now_jpy or 0) > 0),
    ]
    return sum(1 for value in fields if value) / max(1, len(fields))


def _read_baseline_eval_pass() -> bool | None:
    settings = get_settings()
    report_path = Path(settings.baseline_eval_report_path)
    if not report_path.is_absolute():
        report_path = _repo_root() / report_path
    if not report_path.exists():
        return None

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    gates = payload.get("gates") if isinstance(payload, dict) else None
    if not isinstance(gates, dict):
        return None
    value = gates.get("overall_pass")
    return bool(value) if isinstance(value, bool) else None


def _raw_attributes(listing: RawListing) -> dict[str, Any]:
    raw_json = str(listing.raw_attributes_json or "").strip()
    if not raw_json:
        return {}
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_stale_fixture_row(listing: RawListing) -> bool:
    attrs = _raw_attributes(listing)
    return bool(attrs.get("fixture_stale_fallback"))


def _model_age_hours(task: str, now: datetime) -> tuple[str | None, float | None]:
    active = get_active_model_version(task)  # type: ignore[arg-type]
    if not isinstance(active, dict):
        return None, None

    version_id = str(active.get("version_id") or "") or None
    created_at = active.get("created_at")
    if not isinstance(created_at, datetime):
        return version_id, None

    created_at_utc = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - created_at_utc.astimezone(timezone.utc)).total_seconds() / 3600.0)
    return version_id, round(age_hours, 3)


def build_health_metrics(session: Session, window_hours: int) -> HealthMetricsResponse:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=max(1, window_hours))

    listings = session.scalars(
        select(RawListing).where(RawListing.updated_at >= window_start)
    ).all()

    source_counts: dict[str, int] = {}
    for listing in listings:
        source_counts[listing.source] = source_counts.get(listing.source, 0) + 1

    total_recent = len(listings)
    recent_non_stale_listing_count = sum(1 for row in listings if not _is_stale_fixture_row(row))
    parse_completeness_avg = 0.0
    if listings:
        parse_completeness_avg = sum(_parse_completeness(row) for row in listings) / len(listings)

    listing_ids = [listing.listing_id for listing in listings]
    non_discard_count = 0
    if listing_ids:
        rows = session.scalars(
            select(DealScore).where(DealScore.listing_id.in_(listing_ids))
        ).all()
        non_discard_count = sum(1 for row in rows if row.bucket in {"confident", "potential"})

    non_discard_rate = 0.0
    if total_recent > 0:
        non_discard_rate = non_discard_count / total_recent

    reviews = session.scalars(
        select(ManualReview).where(ManualReview.created_at >= window_start)
    ).all()
    manual_review_count = len(reviews)

    false_positive_rate = None
    if manual_review_count > 0:
        fp_count = sum(1 for row in reviews if row.is_false_positive)
        false_positive_rate = fp_count / manual_review_count

    baseline_eval_pass = _read_baseline_eval_pass()
    failure_snapshot = get_operational_failure_snapshot()

    alerts: list[str] = []

    expected_sources = [
        "yahoo_auctions",
        "yahoo_flea_market",
        "mercari",
        "rakuma",
    ]
    for source in expected_sources:
        if source_counts.get(source, 0) < settings.monitoring_min_source_count:
            alerts.append(f"source_low_volume:{source}")

    if parse_completeness_avg < settings.monitoring_min_parse_completeness:
        alerts.append("parse_completeness_low")

    if total_recent > 0 and non_discard_rate < settings.monitoring_min_non_discard_rate:
        alerts.append("non_discard_rate_low")

    if (
        false_positive_rate is not None
        and false_positive_rate > settings.monitoring_max_false_positive_rate
    ):
        alerts.append("false_positive_rate_high")

    if baseline_eval_pass is False:
        alerts.append("baseline_eval_failed")

    latest_non_stale_listing_at = None
    rows_by_recency = session.scalars(select(RawListing).order_by(RawListing.updated_at.desc())).all()
    for row in rows_by_recency:
        if _is_stale_fixture_row(row):
            continue
        latest_non_stale_listing_at = row.updated_at
        break

    listing_freshness_hours: float | None = None
    if latest_non_stale_listing_at is not None:
        ts = latest_non_stale_listing_at
        ts_utc = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        listing_freshness_hours = round(
            max(0.0, (now - ts_utc.astimezone(timezone.utc)).total_seconds() / 3600.0),
            3,
        )

    if listing_freshness_hours is None or listing_freshness_hours > settings.monitoring_max_listing_staleness_hours:
        alerts.append("listing_data_stale")

    active_model_versions: dict[str, str | None] = {}
    model_age_hours: dict[str, float | None] = {}
    for task in ("resale", "auction"):
        version_id, age_hours = _model_age_hours(task, now)
        active_model_versions[task] = version_id
        model_age_hours[task] = age_hours
        if age_hours is not None and age_hours > settings.monitoring_max_model_age_hours:
            alerts.append(f"model_stale:{task}")

    return HealthMetricsResponse(
        generated_at=now,
        window_hours=max(1, window_hours),
        total_recent_listings=total_recent,
        source_counts=source_counts,
        parse_completeness_avg=round(parse_completeness_avg, 4),
        non_discard_rate=round(non_discard_rate, 4),
        manual_review_count=manual_review_count,
        false_positive_rate=(round(false_positive_rate, 4) if false_positive_rate is not None else None),
        baseline_eval_pass=baseline_eval_pass,
        ingestion_failure_count=int(failure_snapshot.get("ingestion_failure_count") or 0),
        latest_ingestion_failure_reason=(
            str(failure_snapshot["latest_ingestion_failure_reason"])
            if failure_snapshot.get("latest_ingestion_failure_reason")
            else None
        ),
        retrain_failure_count=int(failure_snapshot.get("retrain_failure_count") or 0),
        latest_retrain_failure_reason=(
            str(failure_snapshot["latest_retrain_failure_reason"])
            if failure_snapshot.get("latest_retrain_failure_reason")
            else None
        ),
        active_model_versions=active_model_versions,
        model_age_hours=model_age_hours,
        recent_non_stale_listing_count=recent_non_stale_listing_count,
        latest_non_stale_listing_at=latest_non_stale_listing_at,
        listing_freshness_hours=listing_freshness_hours,
        alerts=alerts,
    )
