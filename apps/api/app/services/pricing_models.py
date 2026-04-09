import csv
import json
from functools import lru_cache
from pathlib import Path
from statistics import median

from ..models import RawListing
from .model_registry import resolve_active_artifact_path
from .taxonomy import classification_id_for


@lru_cache(maxsize=1)
def _load_resale_artifact() -> dict:
    artifact_path = resolve_active_artifact_path("resale")
    if not artifact_path.exists():
        return {}
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def _load_auction_artifact() -> dict:
    artifact_path = resolve_active_artifact_path("auction")
    if not artifact_path.exists():
        return {}
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def clear_model_artifact_cache() -> None:
    _load_resale_artifact.cache_clear()
    _load_auction_artifact.cache_clear()
    _load_resale_fallback_heuristics.cache_clear()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _pen_swap_dataset_path() -> Path:
    return _repo_root() / "data" / "labeled" / "pen_swap_sales.csv"


def _dataset_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    stat = path.stat()
    return json.dumps(
        {
            "path": str(path.resolve()),
            "mtime_ns": int(stat.st_mtime_ns),
            "size": int(stat.st_size),
        },
        sort_keys=True,
    )


@lru_cache(maxsize=8)
def _load_resale_fallback_heuristics(dataset_fingerprint: str) -> dict[str, float]:
    if dataset_fingerprint == "missing":
        return {}
    try:
        dataset_meta = json.loads(dataset_fingerprint)
    except json.JSONDecodeError:
        return {}
    dataset_path = Path(str(dataset_meta.get("path") or ""))
    if not dataset_path.exists():
        return {}

    brand_ratios: dict[str, list[float]] = {}
    try:
        with dataset_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                ask = int(float(row.get("ask_price_jpy") or 0))
                sold = int(float(row.get("sold_price_jpy") or 0))
                if ask <= 0 or sold <= 0:
                    continue
                brand = str(row.get("brand") or "Unknown")
                brand_ratios.setdefault(brand, []).append(sold / ask)
    except Exception:
        return {}

    multipliers: dict[str, float] = {}
    for brand, values in brand_ratios.items():
        if len(values) < 2:
            continue
        multipliers[brand] = round(float(median(values)), 4)
    return multipliers


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
    dynamic_multipliers = _load_resale_fallback_heuristics(_dataset_fingerprint(_pen_swap_dataset_path()))
    if dynamic_multipliers:
        multipliers.update(dynamic_multipliers)

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
        line = classification_payload.get("line")
        condition_grade = str(classification_payload.get("condition_grade") or "B")
        item_count = int(classification_payload.get("item_count_estimate") or 1)
        classification_id = str(
            classification_payload.get("classification_id")
            or classification_id_for(brand, line if isinstance(line, str) else None)
        )

        brand_multipliers = artifact.get("brand_multipliers") or {}
        line_multipliers = artifact.get("line_multipliers") or {}
        condition_penalties = artifact.get("condition_penalties") or {}

        line_multiplier = line_multipliers.get(classification_id)
        if line_multiplier is not None:
            multiplier = float(line_multiplier)
        else:
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
        expected = max(expected, current_price + max(120, int(current_price * 0.03)))
        expected = min(expected, int(valuation_payload["resale_pred_jpy"] * 0.92))

        low_win = max(int(current_price * 1.02), current_price + max(60, int(current_price * 0.015)))
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
        expected = max(expected, current_price + max(150, int(current_price * 0.025)))
        low_win = max(low_win, current_price + max(70, int(current_price * 0.012)))

        confidence = min(
            0.93,
            float(artifact.get("confidence_base", 0.58)) + min(0.2, bid_count * 0.02),
        )

    return {
        "auction_low_win_price_jpy": int(low_win),
        "auction_expected_final_price_jpy": int(expected),
        "auction_confidence": round(confidence, 3),
    }
