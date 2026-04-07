import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import DealScore, ManualReview, RawListing
from ..schemas import HealthMetricsResponse


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
        alerts=alerts,
    )
