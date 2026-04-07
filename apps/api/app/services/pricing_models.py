import json
from functools import lru_cache
from pathlib import Path

from ..core.config import get_settings
from ..models import RawListing


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def _load_resale_artifact() -> dict:
    settings = get_settings()
    artifact_path = _repo_root() / settings.resale_model_artifact_path
    if not artifact_path.exists():
        return {}
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def _load_auction_artifact() -> dict:
    settings = get_settings()
    artifact_path = _repo_root() / settings.auction_model_artifact_path
    if not artifact_path.exists():
        return {}
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _heuristic_resale_prediction(listing: RawListing, classification_payload: dict) -> tuple[int, int, int, float]:
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

    confidence = min(
        0.94,
        0.45
        + (0.15 if brand != "Unknown" else 0.0)
        + (classification_payload["classification_confidence"] * 0.32),
    )
    return resale_pred, low, high, confidence


def _bucket_for_bid_count(bid_count: int) -> str:
    if bid_count <= 0:
        return "0"
    if bid_count <= 3:
        return "1_3"
    if bid_count <= 7:
        return "4_7"
    return "8_plus"


def predict_resale_value(
    listing: RawListing,
    classification_payload: dict,
) -> dict:
    artifact = _load_resale_artifact()
    if not artifact:
        resale_pred, low, high, valuation_confidence = _heuristic_resale_prediction(
            listing,
            classification_payload,
        )
    else:
        base_price = listing.price_buy_now_jpy or listing.current_price_jpy or 12000
        brand = str(classification_payload.get("brand") or "Unknown")
        condition_grade = str(classification_payload.get("condition_grade") or "B")
        item_count = int(classification_payload.get("item_count_estimate") or 1)

        brand_multipliers = artifact.get("brand_multipliers") or {}
        condition_penalties = artifact.get("condition_penalties") or {}

        multiplier = float(brand_multipliers.get(brand, artifact.get("default_multiplier", 1.3)))
        lot_item_uplift = float(artifact.get("lot_item_uplift", 0.68))
        resale_pred = int(base_price * multiplier)
        if item_count > 1:
            resale_pred = int(resale_pred * (1 + lot_item_uplift * (item_count - 1)))

        grade_penalty = float(condition_penalties.get(condition_grade, artifact.get("default_condition_penalty", 0.85)))
        resale_pred = int(resale_pred * grade_penalty)

        ci_pct = float(artifact.get("ci_pct", 0.15))
        ci_margin = max(1500, int(resale_pred * ci_pct))
        low = max(1000, resale_pred - ci_margin)
        high = resale_pred + ci_margin

        valuation_confidence = min(
            0.95,
            float(artifact.get("confidence_base", 0.52))
            + (float(classification_payload.get("classification_confidence") or 0.0) * 0.35),
        )

    return {
        "resale_pred_jpy": resale_pred,
        "resale_ci_low_jpy": low,
        "resale_ci_high_jpy": high,
        "valuation_confidence": round(valuation_confidence, 3),
    }


def predict_auction_value(
    listing: RawListing,
    valuation_payload: dict,
) -> dict | None:
    if listing.listing_format != "auction":
        return None

    current_price = max(1, listing.current_price_jpy)
    bid_count = listing.bid_count or 0

    artifact = _load_auction_artifact()
    if not artifact:
        expected = int(current_price * (1.08 + min(0.25, bid_count * 0.03)))
        expected = max(expected, current_price + 500)
        expected = min(expected, int(valuation_payload["resale_pred_jpy"] * 0.92))

        low_win = max(int(current_price * 1.02), current_price + 200)
        confidence = min(0.91, 0.55 + min(0.25, bid_count * 0.04))
    else:
        bucket = _bucket_for_bid_count(bid_count)
        expected_multipliers = artifact.get("bid_bucket_expected_multipliers") or {}
        low_multipliers = artifact.get("bid_bucket_low_multipliers") or {}
        expected_multiplier = float(expected_multipliers.get(bucket, artifact.get("default_expected_multiplier", 1.12)))
        low_multiplier = float(low_multipliers.get(bucket, artifact.get("default_low_multiplier", 1.03)))

        expected = int(current_price * expected_multiplier)
        low_win = int(current_price * low_multiplier)

        cap_ratio = float(artifact.get("max_resale_ratio", 0.92))
        expected = min(expected, int(valuation_payload["resale_pred_jpy"] * cap_ratio))
        expected = max(expected, current_price + 300)
        low_win = max(low_win, current_price + 100)

        confidence = min(
            0.93,
            float(artifact.get("confidence_base", 0.58)) + min(0.2, bid_count * 0.02),
        )

    return {
        "auction_low_win_price_jpy": int(low_win),
        "auction_expected_final_price_jpy": int(expected),
        "auction_confidence": round(confidence, 3),
    }
