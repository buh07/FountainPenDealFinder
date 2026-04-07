import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.fixture_source import FixtureListingSourceAdapter
from ..adapters.yahoo_auctions import YahooAuctionsAdapter
from ..core.config import get_settings
from ..models import (
    AuctionPrediction,
    ClassificationResult,
    DealScore,
    ProxyOptionEstimate,
    RawListing,
    ValuationPrediction,
)


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


def load_marketplace_listings() -> list[dict[str, Any]]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []

    if settings.yahoo_auctions_enabled:
        yahoo = YahooAuctionsAdapter()
        try:
            rows.extend(yahoo.get_fresh_window_listings(day_start, category="fountain_pen"))
            rows.extend(
                yahoo.get_ending_auctions(
                    window_start=now,
                    window_end=now + timedelta(hours=24),
                    category="fountain_pen",
                )
            )
        except Exception:
            # Keep the pipeline alive if Yahoo temporarily blocks or changes layout.
            pass

    if (not rows) and settings.use_fixture_fallback:
        fixture = FixtureListingSourceAdapter()
        rows.extend(fixture.get_fresh_window_listings(day_start, category="fountain_pen"))
        rows.extend(
            fixture.get_ending_auctions(
                window_start=now,
                window_end=now + timedelta(hours=24),
                category="fountain_pen",
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
    return existing


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


def predict_resale_value(
    listing: RawListing,
    classification_payload: dict[str, Any],
) -> dict[str, Any]:
    multipliers = {
        "Pilot": 1.75,
        "Namiki": 2.0,
        "Sailor": 1.6,
        "Platinum": 1.5,
        "Nakaya": 1.9,
        "Pelikan": 1.7,
        "Montblanc": 1.85,
        "Unknown": 1.3,
    }

    base_price = listing.price_buy_now_jpy or listing.current_price_jpy or 12000
    brand = classification_payload["brand"]
    multiplier = multipliers.get(brand, 1.3)

    resale_pred = int(base_price * multiplier)
    item_count = classification_payload["item_count_estimate"]
    if item_count > 1:
        resale_pred = int(resale_pred * (1 + 0.68 * (item_count - 1)))

    grade_penalty = {
        "A": 1.0,
        "B+": 0.95,
        "B": 0.9,
        "C": 0.75,
        "Parts/Repair": 0.45,
    }
    resale_pred = int(resale_pred * grade_penalty.get(classification_payload["condition_grade"], 0.85))

    ci_margin = max(2000, int(resale_pred * 0.15))
    low = max(1000, resale_pred - ci_margin)
    high = resale_pred + ci_margin

    valuation_confidence = min(
        0.94,
        0.45
        + (0.15 if brand != "Unknown" else 0.0)
        + (classification_payload["classification_confidence"] * 0.32),
    )

    return {
        "resale_pred_jpy": resale_pred,
        "resale_ci_low_jpy": low,
        "resale_ci_high_jpy": high,
        "valuation_confidence": round(valuation_confidence, 3),
    }


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


def predict_auction_value(
    listing: RawListing,
    valuation_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if listing.listing_format != "auction":
        return None

    current_price = max(1, listing.current_price_jpy)
    bid_count = listing.bid_count or 0
    expected = int(current_price * (1.08 + min(0.25, bid_count * 0.03)))
    expected = max(expected, current_price + 500)
    expected = min(expected, int(valuation_payload["resale_pred_jpy"] * 0.92))

    low_win = max(int(current_price * 1.02), current_price + 200)
    confidence = min(0.91, 0.55 + min(0.25, bid_count * 0.04))

    return {
        "auction_low_win_price_jpy": int(low_win),
        "auction_expected_final_price_jpy": int(expected),
        "auction_confidence": round(confidence, 3),
    }


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


def estimate_proxy_options(listing: RawListing, buy_price_jpy: int) -> list[dict[str, Any]]:
    proxy_rules = [
        {"name": "Buyee", "service_fee_jpy": 300, "coupon_sources": {"rakuma", "yahoo_flea_market"}},
        {"name": "FromJapan", "service_fee_jpy": 250, "coupon_sources": {"mercari"}},
        {"name": "Neokyo", "service_fee_jpy": 275, "coupon_sources": set()},
    ]

    domestic_shipping = listing.domestic_shipping_jpy or 1200
    intl_shipping = 1800

    options: list[dict[str, Any]] = []
    for rule in proxy_rules:
        coupon_discount = 0
        coupon_id = None

        if listing.source in rule["coupon_sources"]:
            coupon_discount = int(rule["service_fee_jpy"] * 0.5)
            coupon_id = f"{rule['name'].lower()}_servicefee50"

        if buy_price_jpy >= 60000 and rule["name"] == "FromJapan":
            coupon_discount += 500
            coupon_id = (coupon_id or "fromjapan_item500")

        total_cost = max(
            0,
            buy_price_jpy + domestic_shipping + rule["service_fee_jpy"] + intl_shipping - coupon_discount,
        )

        options.append(
            {
                "proxy_name": rule["name"],
                "total_cost_jpy": int(total_cost),
                "coupon_id": coupon_id,
                "coupon_discount_jpy": int(coupon_discount),
                "cost_confidence": 0.74 if coupon_discount == 0 else 0.66,
                "is_recommended": False,
            }
        )

    options.sort(key=lambda item: item["total_cost_jpy"])
    if options:
        options[0]["is_recommended"] = True
    return options


def _upsert_proxy_options(
    session: Session,
    listing_id: str,
    payloads: list[dict[str, Any]],
) -> list[ProxyOptionEstimate]:
    existing_rows = session.scalars(
        select(ProxyOptionEstimate).where(ProxyOptionEstimate.listing_id == listing_id)
    ).all()
    existing_by_name = {row.proxy_name: row for row in existing_rows}

    rows: list[ProxyOptionEstimate] = []
    incoming_names = {payload["proxy_name"] for payload in payloads}
    for payload in payloads:
        row = existing_by_name.get(payload["proxy_name"])
        if row is None:
            row = ProxyOptionEstimate(listing_id=listing_id, proxy_name=payload["proxy_name"])

        row.total_cost_jpy = payload["total_cost_jpy"]
        row.coupon_id = payload["coupon_id"]
        row.coupon_discount_jpy = payload["coupon_discount_jpy"]
        row.cost_confidence = payload["cost_confidence"]
        row.is_recommended = payload["is_recommended"]
        session.add(row)
        rows.append(row)

    for row in existing_rows:
        if row.proxy_name not in incoming_names:
            session.delete(row)

    session.flush()
    return rows


def compute_score(
    listing: RawListing,
    classification_payload: dict[str, Any],
    valuation_payload: dict[str, Any],
    auction_payload: dict[str, Any] | None,
    proxy_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()

    best_proxy = next((opt for opt in proxy_payloads if opt["is_recommended"]), proxy_payloads[0])
    expected_profit = valuation_payload["resale_pred_jpy"] - best_proxy["total_cost_jpy"]
    expected_profit_pct = expected_profit / max(best_proxy["total_cost_jpy"], 1)

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
        f"best proxy {best_proxy['proxy_name']} at {best_proxy['total_cost_jpy']} JPY."
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
    proxy_payloads = estimate_proxy_options(listing, int(buy_price_for_proxy or 0))
    proxy_rows = _upsert_proxy_options(session, listing.listing_id, proxy_payloads)

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
    for payload in source_rows:
        listing = upsert_raw_listing(session, payload)
        artifacts = score_single_listing(session, listing)
        ingested_count += 1
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
        "report_path": report.report_path,
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
