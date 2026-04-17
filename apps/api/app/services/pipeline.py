import hashlib
import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.fixture_source import FixtureListingSourceAdapter
from ..adapters.html_helpers import extract_price_jpy
from ..adapters.mercari import MercariAdapter
from ..adapters.rakuma import RakumaAdapter
from ..adapters.yahoo_auctions import YahooAuctionsAdapter
from ..adapters.yahoo_flea_market import YahooFleaMarketAdapter
from ..core.config import get_settings
from ..models import (
    AuctionPrediction,
    ClassificationResult,
    DealScore,
    ListingImage,
    ListingSnapshot,
    RawListing,
    ValuationPrediction,
)
from .listing_quality import derive_price_status, get_default_timezone, has_positive_price, parse_raw_attributes, to_utc
from .classification_pipeline import classify_listing_multi_stage
from .confidence_calibration import calibrate_classification_confidence
from .object_store import capture_listing_assets
from .ops_telemetry import record_ingestion_failure
from .pricing_models import predict_auction_value, predict_resale_value
from .proxy_tracker import estimate_proxy_deals, upsert_proxy_deals


logger = logging.getLogger(__name__)


SOURCE_ADAPTER_FACTORIES = {
    "yahoo_auctions": YahooAuctionsAdapter,
    "yahoo_flea_market": YahooFleaMarketAdapter,
    "mercari": MercariAdapter,
    "rakuma": RakumaAdapter,
}


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return to_utc(parsed)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in rows:
        source = str(item.get("source") or item.get("marketplace") or "unknown")
        source_listing_id = str(item.get("source_listing_id") or item.get("listing_id") or "")
        if not source_listing_id:
            continue
        deduped[(source, source_listing_id)] = item
    return list(deduped.values())


def _raw_attributes_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_attributes = payload.get("raw_attributes")
    if isinstance(raw_attributes, dict):
        return dict(raw_attributes)
    return {}


def _set_payload_raw_attributes(payload: dict[str, Any], raw_attributes: dict[str, Any]) -> None:
    payload["raw_attributes"] = raw_attributes


def _payload_price_status(payload: dict[str, Any]) -> str:
    return derive_price_status(
        payload.get("current_price_jpy"),
        payload.get("price_buy_now_jpy"),
        _raw_attributes_from_payload(payload),
    )


def _row_completeness(payload: dict[str, Any]) -> float:
    raw_attributes = _raw_attributes_from_payload(payload)
    has_price_data = has_positive_price(
        payload.get("current_price_jpy"),
        payload.get("price_buy_now_jpy"),
    ) or bool(raw_attributes.get("price_parse_error"))

    fields = [
        bool(payload.get("source_listing_id") or payload.get("listing_id")),
        bool(payload.get("url")),
        bool(payload.get("title")),
        bool(payload.get("listing_format") or payload.get("listing_type")),
        has_price_data,
    ]
    return sum(1 for field in fields if field) / max(1, len(fields))


def _filter_parse_complete_rows(
    rows: list[dict[str, Any]],
    min_completeness: float,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for row in rows:
        if _row_completeness(row) >= min_completeness:
            valid.append(row)
    return valid


def _collect_with_retries(
    fetch_fn,
    attempts: int,
    backoff_seconds: float,
    min_completeness: float,
    min_valid_rows: int,
    source_label: str = "unknown_source",
) -> list[dict[str, Any]]:
    safe_attempts = max(1, attempts)
    last_valid: list[dict[str, Any]] = []
    for attempt_index in range(safe_attempts):
        try:
            fetched = fetch_fn()
            candidate_rows = _filter_parse_complete_rows(
                fetched if isinstance(fetched, list) else [],
                min_completeness=min_completeness,
            )
            if len(candidate_rows) >= min_valid_rows:
                return candidate_rows
            last_valid = candidate_rows
            if min_valid_rows > 0:
                record_ingestion_failure(
                    f"{source_label}:insufficient_valid_rows:{len(candidate_rows)}/{min_valid_rows}"
                )
        except Exception:
            logger.exception(
                "Marketplace fetch failed on retry",
                extra={
                    "attempt_index": attempt_index + 1,
                    "attempt_total": safe_attempts,
                },
            )
            record_ingestion_failure(f"{source_label}:fetch_exception")

        if attempt_index < safe_attempts - 1:
            sleep_seconds = max(0.0, backoff_seconds) * (2**attempt_index)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    return last_valid


def _apply_text_price_repair(payload: dict[str, Any], raw_attributes: dict[str, Any]) -> bool:
    text_blob = " ".join(
        str(part or "")
        for part in [
            payload.get("title"),
            payload.get("description_raw"),
            payload.get("condition_text"),
        ]
    )
    repaired_price = extract_price_jpy(text_blob)
    if repaired_price is None or repaired_price <= 0:
        return False

    payload["current_price_jpy"] = int(repaired_price)
    listing_format = str(payload.get("listing_format") or payload.get("listing_type") or "")
    if listing_format == "buy_now":
        payload["price_buy_now_jpy"] = int(repaired_price)

    raw_attributes.pop("price_parse_error", None)
    raw_attributes["price_repaired_from"] = "text_fallback"
    return True


def _apply_detail_price_repair(payload: dict[str, Any], raw_attributes: dict[str, Any]) -> bool:
    source = str(payload.get("source") or payload.get("marketplace") or "")
    source_listing_id = str(payload.get("source_listing_id") or payload.get("listing_id") or "")
    factory = SOURCE_ADAPTER_FACTORIES.get(source)
    if factory is None or not source_listing_id:
        return False

    try:
        detail = factory().fetch_listing_detail(source_listing_id)
    except Exception:
        logger.exception(
            "Detail price repair fetch failed",
            extra={
                "source": source,
                "source_listing_id": source_listing_id,
            },
        )
        record_ingestion_failure(f"{source}:detail_price_repair_fetch_exception")
        return False
    if not detail:
        return False

    detail_price = int(detail.get("price_buy_now_jpy") or detail.get("current_price_jpy") or 0)
    if detail_price > 0:
        payload["current_price_jpy"] = detail_price
        listing_format = str(payload.get("listing_format") or payload.get("listing_type") or "")
        if listing_format == "buy_now":
            payload["price_buy_now_jpy"] = detail_price

        raw_attributes.pop("price_parse_error", None)
        raw_attributes["price_repaired_from"] = "detail_fetch"
        return True

    detail_raw_attributes = parse_raw_attributes(detail.get("raw_attributes"))
    if detail_raw_attributes.get("price_parse_error"):
        raw_attributes["price_parse_error"] = True
    return False


def _prepare_listing_payload(payload: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(payload)
    raw_attributes = _raw_attributes_from_payload(prepared)
    _set_payload_raw_attributes(prepared, raw_attributes)

    if _payload_price_status(prepared) == "parse_error":
        raw_attributes["price_repair_attempted"] = True
        if not _apply_text_price_repair(prepared, raw_attributes):
            _apply_detail_price_repair(prepared, raw_attributes)

    return prepared


def _filter_known_ending_rows(
    rows: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        ends_at = _parse_datetime(str(row.get("ends_at") or ""))
        if ends_at is None:
            continue
        if window_start <= ends_at < window_end:
            filtered.append(row)
    return filtered


def load_marketplace_listings() -> list[dict[str, Any]]:
    settings = get_settings()
    default_tz = get_default_timezone(settings.default_timezone)

    local_now = datetime.now(default_tz)
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_local.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    fixture = FixtureListingSourceAdapter()

    def collect_source(
        source_name: str,
        enabled: bool,
        adapter: Any,
        has_ending_auctions: bool,
    ) -> list[dict[str, Any]]:
        source_rows: list[dict[str, Any]] = []

        if enabled:
            source_rows.extend(
                _collect_with_retries(
                    fetch_fn=lambda: adapter.get_fresh_window_listings(
                        day_start_utc,
                        category="fountain_pen",
                    ),
                    attempts=settings.ingestion_retry_attempts,
                    backoff_seconds=settings.ingestion_retry_backoff_seconds,
                    min_completeness=settings.ingestion_parse_min_completeness,
                    min_valid_rows=settings.ingestion_parse_min_valid_rows,
                    source_label=f"{source_name}:fresh",
                )
            )

            if has_ending_auctions:
                source_rows.extend(
                    _collect_with_retries(
                        fetch_fn=lambda: adapter.get_ending_auctions(
                            window_start=now,
                            window_end=now + timedelta(hours=24),
                            category="fountain_pen",
                        ),
                        attempts=settings.ingestion_retry_attempts,
                        backoff_seconds=settings.ingestion_retry_backoff_seconds,
                        min_completeness=settings.ingestion_parse_min_completeness,
                        min_valid_rows=0,
                        source_label=f"{source_name}:ending",
                    )
                )

        if (not source_rows) and settings.use_fixture_fallback:
            source_rows.extend(
                fixture.get_fresh_window_listings(
                    day_start_utc,
                    category="fountain_pen",
                    source_filter=source_name,
                )
            )
            if has_ending_auctions:
                source_rows.extend(
                    fixture.get_ending_auctions(
                        window_start=now,
                        window_end=now + timedelta(hours=24),
                        category="fountain_pen",
                        source_filter=source_name,
                    )
                )

        return source_rows

    rows.extend(
        collect_source(
            source_name="yahoo_auctions",
            enabled=settings.yahoo_auctions_enabled,
            adapter=YahooAuctionsAdapter(),
            has_ending_auctions=True,
        )
    )
    rows.extend(
        collect_source(
            source_name="yahoo_flea_market",
            enabled=settings.yahoo_flea_market_enabled,
            adapter=YahooFleaMarketAdapter(),
            has_ending_auctions=False,
        )
    )
    rows.extend(
        collect_source(
            source_name="mercari",
            enabled=settings.mercari_enabled,
            adapter=MercariAdapter(),
            has_ending_auctions=False,
        )
    )
    rows.extend(
        collect_source(
            source_name="rakuma",
            enabled=settings.rakuma_enabled,
            adapter=RakumaAdapter(),
            has_ending_auctions=False,
        )
    )

    return _dedupe_rows(rows)


def load_ending_auction_rows(window_hours: int) -> list[dict[str, Any]]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=max(1, window_hours))

    fixture = FixtureListingSourceAdapter()
    rows: list[dict[str, Any]] = []

    if settings.yahoo_auctions_enabled:
        yahoo = YahooAuctionsAdapter()
        rows.extend(
            _collect_with_retries(
                fetch_fn=lambda: yahoo.get_ending_auctions(
                    window_start=now,
                    window_end=window_end,
                    category="fountain_pen",
                ),
                attempts=settings.ingestion_retry_attempts,
                backoff_seconds=settings.ingestion_retry_backoff_seconds,
                min_completeness=settings.ingestion_parse_min_completeness,
                min_valid_rows=0,
                source_label="yahoo_auctions:ending_refresh",
            )
        )

    if (not rows) and settings.use_fixture_fallback:
        rows.extend(
            fixture.get_ending_auctions(
                window_start=now,
                window_end=window_end,
                category="fountain_pen",
                source_filter="yahoo_auctions",
            )
        )

    deduped = _dedupe_rows(rows)
    return _filter_known_ending_rows(deduped, window_start=now, window_end=window_end)


def upsert_raw_listing(session: Session, payload: dict[str, Any]) -> RawListing:
    source = payload.get("source") or payload.get("marketplace") or "unknown"
    source_listing_id = str(payload.get("source_listing_id") or payload.get("listing_id") or "")

    existing = session.scalar(
        select(RawListing).where(
            RawListing.source == source,
            RawListing.source_listing_id == source_listing_id,
        )
    )

    if existing is None:
        existing = RawListing(source=source, source_listing_id=source_listing_id)

    existing.url = str(payload.get("url") or "")
    existing.title = str(payload.get("title") or "")
    existing.description_raw = str(payload.get("description_raw") or "")
    existing.images_json = _to_json(payload.get("images") or [])
    existing.seller_id = payload.get("seller_id")
    existing.seller_rating = payload.get("seller_rating")
    existing.listing_format = payload.get("listing_format") or payload.get("listing_type") or "buy_now"
    existing.current_price_jpy = int(payload.get("current_price_jpy") or 0)

    buy_now = payload.get("price_buy_now_jpy")
    existing.price_buy_now_jpy = int(buy_now) if buy_now not in (None, "") else None

    existing.domestic_shipping_jpy = int(payload.get("domestic_shipping_jpy") or 0)
    existing.bid_count = payload.get("bid_count")
    existing.listed_at = _parse_datetime(payload.get("listed_at"))
    existing.ends_at = _parse_datetime(payload.get("ends_at"))
    existing.location_prefecture = payload.get("location_prefecture")
    existing.condition_text = payload.get("condition_text")
    existing.lot_size_hint = int(payload.get("lot_size_hint") or 1)
    existing.raw_attributes_json = _to_json(payload.get("raw_attributes") or {})

    session.add(existing)
    session.flush()

    _upsert_listing_images(session, existing.listing_id, payload.get("images") or [])
    _record_listing_snapshot(session, existing)

    return existing


def _upsert_listing_images(session: Session, listing_id: str, images: list[Any]) -> None:
    normalized: list[str] = []
    for image in images:
        value = str(image or "").strip()
        if value:
            normalized.append(value)

    if not normalized:
        return

    existing_urls = {
        row.image_url
        for row in session.scalars(
            select(ListingImage).where(ListingImage.listing_id == listing_id)
        ).all()
    }

    for index, image_url in enumerate(normalized):
        if image_url in existing_urls:
            continue
        session.add(
            ListingImage(
                listing_id=listing_id,
                image_url=image_url,
                image_order=index,
            )
        )

    session.flush()


def _record_listing_snapshot(session: Session, listing: RawListing) -> None:
    snapshot_payload = {
        "current_price_jpy": listing.current_price_jpy,
        "price_buy_now_jpy": listing.price_buy_now_jpy,
        "bid_count": listing.bid_count,
        "ends_at": listing.ends_at.isoformat() if listing.ends_at else None,
        "raw_attributes_json": listing.raw_attributes_json,
    }
    snapshot_hash = hashlib.sha256(
        json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    exists = session.scalar(
        select(ListingSnapshot.snapshot_id).where(
            ListingSnapshot.listing_id == listing.listing_id,
            ListingSnapshot.snapshot_hash == snapshot_hash,
        )
    )
    if exists:
        return

    session.add(
        ListingSnapshot(
            listing_id=listing.listing_id,
            snapshot_hash=snapshot_hash,
            current_price_jpy=listing.current_price_jpy,
            price_buy_now_jpy=listing.price_buy_now_jpy,
            bid_count=listing.bid_count,
            ends_at=listing.ends_at,
            raw_attributes_json=listing.raw_attributes_json,
        )
    )
    session.flush()


def classify_listing(listing: RawListing) -> dict[str, Any]:
    return classify_listing_multi_stage(listing)


def _upsert_classification(
    session: Session,
    listing_id: str,
    payload: dict[str, Any],
) -> ClassificationResult:
    row = session.scalar(
        select(ClassificationResult).where(ClassificationResult.listing_id == listing_id)
    )
    if row is None:
        row = ClassificationResult(listing_id=listing_id)

    row.classification_id = payload["classification_id"]
    row.brand = payload["brand"]
    row.line = payload["line"]
    row.nib_material = payload["nib_material"]
    row.nib_size = payload["nib_size"]
    row.condition_grade = payload["condition_grade"]
    row.condition_flags_json = _to_json(payload["condition_flags"])
    row.item_count_estimate = payload["item_count_estimate"]
    row.items_json = _to_json(payload["items"])
    row.classification_confidence = payload["classification_confidence"]
    row.condition_confidence = payload["condition_confidence"]
    row.lot_decomposition_confidence = payload["lot_decomposition_confidence"]
    row.text_evidence = payload["text_evidence"]
    row.image_evidence = payload["image_evidence"]

    session.add(row)
    session.flush()
    return row


def _upsert_valuation(
    session: Session,
    listing_id: str,
    payload: dict[str, Any],
) -> ValuationPrediction:
    row = session.scalar(select(ValuationPrediction).where(ValuationPrediction.listing_id == listing_id))
    if row is None:
        row = ValuationPrediction(listing_id=listing_id)

    row.resale_pred_jpy = payload["resale_pred_jpy"]
    row.resale_ci_low_jpy = payload["resale_ci_low_jpy"]
    row.resale_ci_high_jpy = payload["resale_ci_high_jpy"]
    row.valuation_confidence = payload["valuation_confidence"]

    session.add(row)
    session.flush()
    return row


def _upsert_auction(
    session: Session,
    listing_id: str,
    payload: dict[str, Any] | None,
) -> AuctionPrediction | None:
    row = session.scalar(select(AuctionPrediction).where(AuctionPrediction.listing_id == listing_id))

    if payload is None:
        if row is not None:
            session.delete(row)
            session.flush()
        return None

    if row is None:
        row = AuctionPrediction(listing_id=listing_id)

    row.auction_low_win_price_jpy = payload["auction_low_win_price_jpy"]
    row.auction_expected_final_price_jpy = payload["auction_expected_final_price_jpy"]
    row.auction_confidence = payload["auction_confidence"]

    session.add(row)
    session.flush()
    return row


def _dedupe_flags(flags: list[str]) -> list[str]:
    deduped: list[str] = []
    for flag in flags:
        if flag not in deduped:
            deduped.append(flag)
    return deduped


def _zero_valuation_payload() -> dict[str, Any]:
    return {
        "resale_pred_jpy": 0,
        "resale_ci_low_jpy": 0,
        "resale_ci_high_jpy": 0,
        "valuation_confidence": 0.0,
    }


def compute_score(
    listing: RawListing,
    classification_payload: dict[str, Any],
    valuation_payload: dict[str, Any],
    auction_payload: dict[str, Any] | None,
    proxy_payloads: list[dict[str, Any]],
    price_status: str,
) -> dict[str, Any]:
    settings = get_settings()

    best_proxy = next((opt for opt in proxy_payloads if opt["is_recommended"]), None)
    if best_proxy is None and proxy_payloads:
        best_proxy = proxy_payloads[0]

    expected_profit = int(best_proxy["expected_profit_jpy"]) if best_proxy else 0
    expected_profit_pct = float(best_proxy["expected_profit_pct"]) if best_proxy else 0.0

    images = _from_json(listing.images_json, [])
    listing_quality_conf = 0.35
    if listing.description_raw:
        listing_quality_conf += 0.2
    if len(images) >= 3:
        listing_quality_conf += 0.2
    if listing.condition_text:
        listing_quality_conf += 0.15
    if listing.seller_id:
        listing_quality_conf += 0.1
    listing_quality_conf = min(1.0, listing_quality_conf)

    auction_conf = auction_payload["auction_confidence"] if auction_payload else 0.7
    coupon_conf = float(best_proxy["cost_confidence"]) if best_proxy else 0.65

    confidence_overall = (
        0.25 * classification_payload["classification_confidence"]
        + 0.15 * classification_payload["condition_confidence"]
        + 0.10 * classification_payload["lot_decomposition_confidence"]
        + 0.25 * valuation_payload["valuation_confidence"]
        + 0.15 * auction_conf
        + 0.05 * coupon_conf
        + 0.05 * listing_quality_conf
    )
    confidence_overall = round(max(0.0, min(1.0, confidence_overall)), 3)

    risk_flags = list(classification_payload["condition_flags"])
    risk_flags.extend(classification_payload.get("uncertainty_tags", []))
    if classification_payload["brand"] == "Unknown":
        risk_flags.append("brand_uncertain")
    if classification_payload["item_count_estimate"] > 1:
        risk_flags.append("lot_uncertainty")
    if classification_payload["classification_confidence"] < 0.55:
        risk_flags.append("model_ambiguity")

    if price_status == "missing":
        risk_flags.append("price_missing")
        risk_flags = _dedupe_flags(risk_flags)
        return {
            "expected_profit_jpy": 0,
            "expected_profit_pct": 0.0,
            "risk_adjusted_profit_jpy": 0,
            "confidence_overall": round(min(confidence_overall, 0.2), 3),
            "bucket": "discard",
            "risk_flags": risk_flags,
            "rationale": "Listing discarded because no valid price was found.",
        }

    if price_status == "parse_error":
        risk_flags.extend(["price_parse_error", "needs_manual_price_review"])
        risk_flags = _dedupe_flags(risk_flags)
        return {
            "expected_profit_jpy": 0,
            "expected_profit_pct": 0.0,
            "risk_adjusted_profit_jpy": 0,
            "confidence_overall": round(min(confidence_overall, 0.35), 3),
            "bucket": "potential",
            "risk_flags": risk_flags,
            "rationale": "Price parsing failed; held for manual review with neutralized profit.",
        }

    major_flags = {"hairline_crack", "bent_nib_possible", "possible_fake", "parts_repair"}
    has_major_risk = any(flag in major_flags for flag in risk_flags)

    profit_threshold_ok = (
        expected_profit >= settings.min_profit_jpy
        and expected_profit_pct >= settings.min_profit_pct
    )

    if not profit_threshold_ok:
        bucket = "discard"
    elif confidence_overall >= settings.confident_min and not has_major_risk:
        bucket = "confident"
    elif confidence_overall >= settings.potential_min:
        bucket = "potential"
    else:
        bucket = "discard"

    risk_adjusted_profit = int(expected_profit * confidence_overall)
    proxy_name = best_proxy["proxy_name"] if best_proxy else "None"
    proxy_total = int(best_proxy["total_cost_jpy"]) if best_proxy else 0
    proxy_profit = int(best_proxy["expected_profit_jpy"]) if best_proxy else 0
    rationale = (
        f"{classification_payload['brand']} {classification_payload['line'] or 'fountain pen'} "
        f"estimated resale {valuation_payload['resale_pred_jpy']} JPY, "
        f"best proxy {proxy_name} at {proxy_total} JPY "
        f"(expected proxy profit {proxy_profit} JPY)."
    )

    return {
        "expected_profit_jpy": int(expected_profit),
        "expected_profit_pct": round(expected_profit_pct, 4),
        "risk_adjusted_profit_jpy": int(risk_adjusted_profit),
        "confidence_overall": confidence_overall,
        "bucket": bucket,
        "risk_flags": _dedupe_flags(risk_flags),
        "rationale": rationale,
    }


def _upsert_deal_score(
    session: Session,
    listing_id: str,
    payload: dict[str, Any],
) -> DealScore:
    row = session.scalar(select(DealScore).where(DealScore.listing_id == listing_id))
    if row is None:
        row = DealScore(listing_id=listing_id)

    row.expected_profit_jpy = payload["expected_profit_jpy"]
    row.expected_profit_pct = payload["expected_profit_pct"]
    row.risk_adjusted_profit_jpy = payload["risk_adjusted_profit_jpy"]
    row.confidence_overall = payload["confidence_overall"]
    row.bucket = payload["bucket"]
    row.risk_flags_json = _to_json(payload["risk_flags"])
    row.rationale = payload["rationale"]

    session.add(row)
    session.flush()
    return row


def _listing_price_status(listing: RawListing) -> str:
    return derive_price_status(
        listing.current_price_jpy,
        listing.price_buy_now_jpy,
        listing.raw_attributes_json,
    )


def score_single_listing(session: Session, listing: RawListing) -> dict[str, Any]:
    price_status = _listing_price_status(listing)

    classification_payload = classify_listing(listing)
    raw_classification_confidence = float(classification_payload.get("classification_confidence") or 0.0)
    calibrated_confidence, calibration_info = calibrate_classification_confidence(
        session,
        raw_classification_confidence,
    )
    classification_payload["classification_confidence_raw"] = round(raw_classification_confidence, 3)
    classification_payload["classification_confidence"] = round(calibrated_confidence, 3)
    stage_explanations = classification_payload.get("stage_explanations")
    if isinstance(stage_explanations, dict):
        stage_explanations["confidence_calibration"] = calibration_info

    classification_row = _upsert_classification(session, listing.listing_id, classification_payload)

    if price_status == "valid":
        valuation_payload = predict_resale_value(listing, classification_payload)
    else:
        valuation_payload = _zero_valuation_payload()
    valuation_row = _upsert_valuation(session, listing.listing_id, valuation_payload)

    auction_payload = predict_auction_value(listing, valuation_payload) if price_status == "valid" else None
    auction_row = _upsert_auction(session, listing.listing_id, auction_payload)

    buy_price_for_proxy = (
        auction_payload["auction_expected_final_price_jpy"]
        if auction_payload
        else (listing.price_buy_now_jpy or listing.current_price_jpy)
    )
    resale_reference = int(valuation_payload["resale_pred_jpy"])
    if price_status != "valid":
        resale_reference = 0

    proxy_payloads = estimate_proxy_deals(
        session,
        listing,
        buy_price_jpy=int(buy_price_for_proxy or 0),
        resale_reference_jpy=resale_reference,
    )
    proxy_rows = upsert_proxy_deals(session, listing.listing_id, proxy_payloads)

    score_payload = compute_score(
        listing,
        classification_payload,
        valuation_payload,
        auction_payload,
        proxy_payloads,
        price_status=price_status,
    )
    deal_row = _upsert_deal_score(session, listing.listing_id, score_payload)

    return {
        "classification": classification_row,
        "valuation": valuation_row,
        "auction": auction_row,
        "proxy_options": proxy_rows,
        "deal_score": deal_row,
    }


def run_collection_pipeline(session: Session, report_date: date | None = None) -> dict[str, Any]:
    source_rows = load_marketplace_listings()

    ingested_count = 0
    scored_count = 0
    source_counts: dict[str, int] = {}
    for payload in source_rows:
        prepared_payload = _prepare_listing_payload(payload)
        listing = upsert_raw_listing(session, prepared_payload)
        artifacts = score_single_listing(session, listing)
        capture_listing_assets(
            session,
            listing,
            deal_bucket=artifacts["deal_score"].bucket,
            source_payload=prepared_payload,
        )
        ingested_count += 1
        source_counts[listing.source] = source_counts.get(listing.source, 0) + 1
        if artifacts["deal_score"].bucket != "discard":
            scored_count += 1

    session.commit()

    from .reporting import generate_daily_report

    settings = get_settings()
    default_tz = get_default_timezone(settings.default_timezone)
    target_date = report_date or datetime.now(default_tz).date()
    report = generate_daily_report(session, target_date)

    return {
        "ingested_count": ingested_count,
        "scored_count": scored_count,
        "confident_count": len(report.confident),
        "potential_count": len(report.potential),
        "source_counts": source_counts,
        "report_path": report.report_path,
    }


def run_ending_auction_refresh(
    session: Session,
    window_hours: int,
) -> dict[str, Any]:
    source_rows = load_ending_auction_rows(window_hours=window_hours)

    ingested_count = 0
    scored_count = 0
    for payload in source_rows:
        prepared_payload = _prepare_listing_payload(payload)
        listing = upsert_raw_listing(session, prepared_payload)
        artifacts = score_single_listing(session, listing)
        capture_listing_assets(
            session,
            listing,
            deal_bucket=artifacts["deal_score"].bucket,
            source_payload=prepared_payload,
        )
        ingested_count += 1
        if artifacts["deal_score"].bucket != "discard":
            scored_count += 1

    session.commit()
    return {
        "ingested_count": ingested_count,
        "scored_count": scored_count,
        "window_hours": window_hours,
    }


def _priority_score_for_listing(
    listing: RawListing,
    deal_score: DealScore,
    *,
    now: datetime,
    window_hours: int,
    value_signal: float = 0.0,
    rarity_signal: float = 0.0,
) -> float:
    ends_at = to_utc(listing.ends_at)
    if ends_at is None:
        return 0.0

    horizon_seconds = max(1.0, float(window_hours) * 3600.0)
    seconds_to_end = max(0.0, (ends_at - now).total_seconds())
    urgency = max(0.0, min(1.0, 1.0 - (seconds_to_end / horizon_seconds)))

    underpricing_signal = max(0.0, min(1.0, float(deal_score.expected_profit_pct)))
    confidence_signal = max(0.0, min(1.0, float(deal_score.confidence_overall)))
    value_signal_norm = max(0.0, min(1.0, float(value_signal)))
    rarity_signal_norm = max(0.0, min(1.0, float(rarity_signal)))

    return round(
        (0.35 * underpricing_signal)
        + (0.25 * confidence_signal)
        + (0.15 * urgency)
        + (0.15 * value_signal_norm)
        + (0.10 * rarity_signal_norm),
        4,
    )


def select_priority_auction_candidates(
    session: Session,
    *,
    window_hours: int,
    threshold: float,
    limit: int = 100,
) -> list[tuple[RawListing, float]]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=max(1, window_hours))
    value_ceiling = max(1.0, float(settings.priority_value_reference_jpy_ceiling))

    rows = session.execute(
        select(RawListing, DealScore, ValuationPrediction, ClassificationResult)
        .join(DealScore, DealScore.listing_id == RawListing.listing_id)
        .outerjoin(ValuationPrediction, ValuationPrediction.listing_id == RawListing.listing_id)
        .outerjoin(ClassificationResult, ClassificationResult.listing_id == RawListing.listing_id)
        .where(
            RawListing.listing_format == "auction",
            RawListing.ends_at.is_not(None),
            RawListing.ends_at >= now,
            RawListing.ends_at < window_end,
        )
        .order_by(RawListing.ends_at.asc())
    ).all()

    class_counts: Counter[str] = Counter()
    for _listing, _deal_score, _valuation, classification in rows:
        class_key = (
            str(classification.classification_id)
            if classification and classification.classification_id
            else "unknown_fountain_pen"
        )
        class_counts[class_key] += 1

    scored: list[tuple[RawListing, float]] = []
    for listing, deal_score, valuation, classification in rows:
        value_reference = int(
            (valuation.resale_pred_jpy if valuation and valuation.resale_pred_jpy else 0)
            or (listing.price_buy_now_jpy or listing.current_price_jpy or 0)
        )
        value_signal = max(0.0, min(1.0, value_reference / value_ceiling))

        class_key = (
            str(classification.classification_id)
            if classification and classification.classification_id
            else "unknown_fountain_pen"
        )
        class_count = max(1, class_counts.get(class_key, 1))
        rarity_signal = max(0.0, min(1.0, 1.0 / (class_count**0.5)))
        if classification and classification.brand in {"Namiki", "Nakaya", "Montblanc"}:
            rarity_signal = min(1.0, rarity_signal + 0.1)

        score = _priority_score_for_listing(
            listing,
            deal_score,
            now=now,
            window_hours=max(1, window_hours),
            value_signal=value_signal,
            rarity_signal=rarity_signal,
        )
        if score >= threshold:
            scored.append((listing, score))

    scored.sort(
        key=lambda item: (
            item[1],
            to_utc(item[0].ends_at) or datetime.max.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return scored[: max(1, limit)]


def run_priority_auction_refresh(
    session: Session,
    *,
    window_hours: int,
    threshold: float,
    limit: int = 100,
) -> dict[str, Any]:
    candidates = select_priority_auction_candidates(
        session,
        window_hours=window_hours,
        threshold=threshold,
        limit=limit,
    )

    ingested_count = 0
    scored_count = 0
    for listing, _score in candidates:
        factory = SOURCE_ADAPTER_FACTORIES.get(listing.source)
        if factory is None:
            continue
        adapter = factory()

        try:
            payload = adapter.fetch_listing_detail(listing.source_listing_id)
        except Exception:
            logger.exception(
                "Priority detail fetch failed",
                extra={
                    "source": listing.source,
                    "source_listing_id": listing.source_listing_id,
                },
            )
            record_ingestion_failure(f"{listing.source}:priority_detail_fetch_exception")
            continue
        if not payload:
            record_ingestion_failure(f"{listing.source}:priority_detail_missing")
            continue

        prepared_payload = _prepare_listing_payload(payload)
        updated_listing = upsert_raw_listing(session, prepared_payload)
        artifacts = score_single_listing(session, updated_listing)
        capture_listing_assets(
            session,
            updated_listing,
            deal_bucket=artifacts["deal_score"].bucket,
            source_payload=prepared_payload,
        )

        ingested_count += 1
        if artifacts["deal_score"].bucket != "discard":
            scored_count += 1

    session.commit()
    return {
        "candidate_count": len(candidates),
        "ingested_count": ingested_count,
        "scored_count": scored_count,
        "window_hours": max(1, window_hours),
        "threshold": float(threshold),
    }


def _get_listing_or_raise(session: Session, listing_id: str) -> RawListing:
    row = session.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if row is None:
        raise LookupError(f"Listing {listing_id} not found")
    return row


def rescore_listing(session: Session, listing_id: str) -> DealScore:
    listing = _get_listing_or_raise(session, listing_id)
    artifacts = score_single_listing(session, listing)
    session.commit()
    return artifacts["deal_score"]


def predict_resale_for_listing(session: Session, listing_id: str) -> ValuationPrediction | None:
    try:
        listing = _get_listing_or_raise(session, listing_id)
    except LookupError:
        return None

    artifacts = score_single_listing(session, listing)
    session.commit()
    return artifacts["valuation"]


def predict_auction_for_listing(session: Session, listing_id: str) -> AuctionPrediction | None:
    try:
        listing = _get_listing_or_raise(session, listing_id)
    except LookupError:
        return None

    artifacts = score_single_listing(session, listing)
    session.commit()
    return artifacts["auction"]
