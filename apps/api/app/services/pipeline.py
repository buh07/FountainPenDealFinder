import json
import hashlib
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.fixture_source import FixtureListingSourceAdapter
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
from .pricing_models import predict_auction_value, predict_resale_value
from .proxy_tracker import estimate_proxy_deals, upsert_proxy_deals


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
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in rows:
        source = str(item.get("source") or item.get("marketplace") or "unknown")
        source_listing_id = str(item.get("source_listing_id") or item.get("listing_id") or "")
        if not source_listing_id:
            continue
        deduped[(source, source_listing_id)] = item
    return list(deduped.values())


def _row_completeness(payload: dict[str, Any]) -> float:
    fields = [
        bool(payload.get("source_listing_id") or payload.get("listing_id")),
        bool(payload.get("url")),
        bool(payload.get("title")),
        bool(payload.get("listing_format") or payload.get("listing_type")),
        (
            payload.get("current_price_jpy") is not None
            or payload.get("price_buy_now_jpy") is not None
        ),
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
        except Exception:
            pass

        if attempt_index < safe_attempts - 1:
            sleep_seconds = max(0.0, backoff_seconds) * (2**attempt_index)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    return last_valid


def load_marketplace_listings() -> list[dict[str, Any]]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
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
                        day_start,
                        category="fountain_pen",
                    ),
                    attempts=settings.ingestion_retry_attempts,
                    backoff_seconds=settings.ingestion_retry_backoff_seconds,
                    min_completeness=settings.ingestion_parse_min_completeness,
                    min_valid_rows=settings.ingestion_parse_min_valid_rows,
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
                    )
                )

        if (not source_rows) and settings.use_fixture_fallback:
            source_rows.extend(
                fixture.get_fresh_window_listings(
                    day_start,
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
    fixture = FixtureListingSourceAdapter()
    rows: list[dict[str, Any]] = []

    if settings.yahoo_auctions_enabled:
        yahoo = YahooAuctionsAdapter()
        rows.extend(
            _collect_with_retries(
                fetch_fn=lambda: yahoo.get_ending_auctions(
                    window_start=now,
                    window_end=now + timedelta(hours=max(1, window_hours)),
                    category="fountain_pen",
                ),
                attempts=settings.ingestion_retry_attempts,
                backoff_seconds=settings.ingestion_retry_backoff_seconds,
                min_completeness=settings.ingestion_parse_min_completeness,
                min_valid_rows=0,
            )
        )

    if (not rows) and settings.use_fixture_fallback:
        rows.extend(
            fixture.get_ending_auctions(
                window_start=now,
                window_end=now + timedelta(hours=max(1, window_hours)),
                category="fountain_pen",
                source_filter="yahoo_auctions",
            )
        )

    return _dedupe_rows(rows)


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
    existing.price_buy_now_jpy = payload.get("price_buy_now_jpy")
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


BRAND_KEYWORDS = {
    "Namiki": ["namiki", "ナミキ"],
    "Pilot": ["pilot", "パイロット"],
    "Sailor": ["sailor", "セーラー"],
    "Platinum": ["platinum", "プラチナ"],
    "Nakaya": ["nakaya", "中屋"],
    "Pelikan": ["pelikan"],
    "Montblanc": ["montblanc", "モンブラン"],
}


LINE_HINTS = {
    "Custom 743": ["743", "custom 743", "カスタム743"],
    "Custom 823": ["823", "custom 823", "カスタム823"],
    "1911 Large": ["1911", "1911l", "1911 large"],
    "3776 Century": ["3776", "century", "センチュリー"],
    "Yukari": ["yukari", "雪割", "蒔絵"],
}


CONDITION_KEYWORDS = [
    ("傷", "micro_scratches"),
    ("スレ", "micro_scratches"),
    ("scratch", "micro_scratches"),
    ("凹", "dent_or_ding"),
    ("dent", "dent_or_ding"),
    ("メッキ", "plating_wear"),
    ("錆", "trim_wear"),
    ("曲が", "bent_nib_possible"),
    ("割れ", "hairline_crack"),
    ("ヒビ", "hairline_crack"),
    ("ジャンク", "parts_repair"),
    ("repair", "parts_repair"),
    ("名入れ", "name_engraving"),
    ("engraving", "name_engraving"),
    ("漆", "urushi_finish"),
]


def _normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return normalized.strip("_") or "unknown_fountain_pen"


def _detect_brand_and_line(text: str) -> tuple[str, str | None]:
    lower = text.lower()

    detected_brand = "Unknown"
    for brand, keywords in BRAND_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            detected_brand = brand
            break

    detected_line = None
    for line, keywords in LINE_HINTS.items():
        if any(keyword in lower for keyword in keywords):
            detected_line = line
            break

    return detected_brand, detected_line


def _estimate_item_count(text: str, lot_size_hint: int) -> int:
    lower = text.lower()
    match = re.search(r"(\d+)\s*(?:本|pen|pens)", lower)
    if match:
        return max(1, int(match.group(1)))

    if "まとめ" in text or "セット" in text or re.search(r"\blot\b", lower):
        return max(2, lot_size_hint)

    return max(1, lot_size_hint)


def _extract_condition_flags(text: str) -> list[str]:
    lower = text.lower()
    flags = [flag for keyword, flag in CONDITION_KEYWORDS if keyword in lower]
    deduped: list[str] = []
    for flag in flags:
        if flag not in deduped:
            deduped.append(flag)
    return deduped


def classify_listing(listing: RawListing) -> dict[str, Any]:
    text_blob = " ".join(
        part for part in [listing.title, listing.description_raw or "", listing.condition_text or ""] if part
    )
    brand, line = _detect_brand_and_line(text_blob)
    item_count = _estimate_item_count(text_blob, listing.lot_size_hint)
    condition_flags = _extract_condition_flags(text_blob)

    if "parts_repair" in condition_flags:
        condition_grade = "Parts/Repair"
    elif any(flag in condition_flags for flag in ["hairline_crack", "bent_nib_possible"]):
        condition_grade = "C"
    elif any(token in text_blob for token in ["目立った傷や汚れなし", "美品", "good condition"]):
        condition_grade = "B+"
    else:
        condition_grade = "B"

    base_conf = 0.82 if brand != "Unknown" else 0.48
    classification_confidence = max(0.35, min(0.95, base_conf - (0.07 if item_count > 1 else 0.0)))
    condition_confidence = 0.75 if condition_flags or listing.condition_text else 0.45
    lot_decomposition_confidence = 0.9 if item_count == 1 else 0.62

    class_parts = [brand, line or "fountain_pen"]
    classification_id = _normalize_identifier("_".join(class_parts))

    items = []
    for idx in range(item_count):
        items.append(
            {
                "item_index": idx,
                "classification_id": classification_id,
                "condition_grade": condition_grade,
                "condition_flags": condition_flags,
                "visibility_confidence": max(0.3, lot_decomposition_confidence - (idx * 0.03)),
            }
        )

    return {
        "classification_id": classification_id,
        "brand": brand,
        "line": line,
        "nib_material": None,
        "nib_size": None,
        "condition_grade": condition_grade,
        "condition_flags": condition_flags,
        "item_count_estimate": item_count,
        "items": items,
        "classification_confidence": round(classification_confidence, 3),
        "condition_confidence": round(condition_confidence, 3),
        "lot_decomposition_confidence": round(lot_decomposition_confidence, 3),
        "text_evidence": text_blob[:600],
        "image_evidence": None,
    }


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


def compute_score(
    listing: RawListing,
    classification_payload: dict[str, Any],
    valuation_payload: dict[str, Any],
    auction_payload: dict[str, Any] | None,
    proxy_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()

    best_proxy = next((opt for opt in proxy_payloads if opt["is_recommended"]), proxy_payloads[0])
    expected_profit = int(best_proxy["expected_profit_jpy"])
    expected_profit_pct = float(best_proxy["expected_profit_pct"])

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
    coupon_conf = best_proxy["cost_confidence"]

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
    if classification_payload["brand"] == "Unknown":
        risk_flags.append("brand_uncertain")
    if classification_payload["item_count_estimate"] > 1:
        risk_flags.append("lot_uncertainty")
    if classification_payload["classification_confidence"] < 0.55:
        risk_flags.append("model_ambiguity")

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
    rationale = (
        f"{classification_payload['brand']} {classification_payload['line'] or 'fountain pen'} "
        f"estimated resale {valuation_payload['resale_pred_jpy']} JPY, "
        f"best proxy {best_proxy['proxy_name']} at {best_proxy['total_cost_jpy']} JPY "
        f"(expected proxy profit {best_proxy['expected_profit_jpy']} JPY)."
    )

    return {
        "expected_profit_jpy": int(expected_profit),
        "expected_profit_pct": round(expected_profit_pct, 4),
        "risk_adjusted_profit_jpy": int(risk_adjusted_profit),
        "confidence_overall": confidence_overall,
        "bucket": bucket,
        "risk_flags": risk_flags,
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


def score_single_listing(session: Session, listing: RawListing) -> dict[str, Any]:
    classification_payload = classify_listing(listing)
    classification_row = _upsert_classification(session, listing.listing_id, classification_payload)

    valuation_payload = predict_resale_value(listing, classification_payload)
    valuation_row = _upsert_valuation(session, listing.listing_id, valuation_payload)

    auction_payload = predict_auction_value(listing, valuation_payload)
    auction_row = _upsert_auction(session, listing.listing_id, auction_payload)

    buy_price_for_proxy = (
        auction_payload["auction_expected_final_price_jpy"]
        if auction_payload
        else (listing.price_buy_now_jpy or listing.current_price_jpy)
    )
    proxy_payloads = estimate_proxy_deals(
        session,
        listing,
        buy_price_jpy=int(buy_price_for_proxy or 0),
        resale_reference_jpy=int(valuation_payload["resale_pred_jpy"]),
    )
    proxy_rows = upsert_proxy_deals(session, listing.listing_id, proxy_payloads)

    score_payload = compute_score(
        listing,
        classification_payload,
        valuation_payload,
        auction_payload,
        proxy_payloads,
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
        listing = upsert_raw_listing(session, payload)
        artifacts = score_single_listing(session, listing)
        ingested_count += 1
        source_counts[listing.source] = source_counts.get(listing.source, 0) + 1
        if artifacts["deal_score"].bucket != "discard":
            scored_count += 1

    session.commit()

    from .reporting import generate_daily_report

    target_date = report_date or datetime.now(timezone.utc).date()
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
        listing = upsert_raw_listing(session, payload)
        artifacts = score_single_listing(session, listing)
        ingested_count += 1
        if artifacts["deal_score"].bucket != "discard":
            scored_count += 1

    session.commit()
    return {
        "ingested_count": ingested_count,
        "scored_count": scored_count,
        "window_hours": window_hours,
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
