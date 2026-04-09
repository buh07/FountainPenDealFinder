import json
from pathlib import Path

from app.core.config import get_settings
from app.models import RawListing
from app.services.model_registry import promote_candidate_artifact, switch_active_to_version
from app.services.pricing_models import clear_model_artifact_cache, predict_resale_value


def _write_artifact(path: Path, default_multiplier: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact": "resale_baseline",
        "default_multiplier": default_multiplier,
        "brand_multipliers": {},
        "line_multipliers": {},
        "condition_penalties": {"B": 1.0},
        "default_condition_penalty": 1.0,
        "lot_item_uplift": 0.0,
        "ci_pct": 0.1,
        "confidence_base": 0.5,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _listing() -> RawListing:
    return RawListing(
        source="mercari",
        source_listing_id="seed-1",
        url="https://example.com/listing/1",
        title="Test listing",
        description_raw="",
        images_json="[]",
        seller_id="seller-1",
        seller_rating=4.9,
        listing_format="buy_now",
        current_price_jpy=10000,
        price_buy_now_jpy=10000,
        domestic_shipping_jpy=800,
        bid_count=None,
        listed_at=None,
        ends_at=None,
        location_prefecture=None,
        condition_text=None,
        lot_size_hint=1,
        raw_attributes_json="{}",
    )


def test_active_pointer_switch_changes_inference_output(monkeypatch):
    tmp_root = Path("/tmp/fpdf_test_model_registry_inference")
    tmp_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("MODEL_VERSION_ROOT", str(tmp_root / "versions"))
    monkeypatch.setenv("MODEL_ACTIVE_POINTER_RESALE", str(tmp_root / "resale" / "active_pointer.txt"))
    monkeypatch.setenv("MODEL_ACTIVE_POINTER_AUCTION", str(tmp_root / "auction" / "active_pointer.txt"))
    monkeypatch.setenv("RESALE_MODEL_ARTIFACT_PATH", str(tmp_root / "resale" / "baseline_v1.json"))
    monkeypatch.setenv("AUCTION_MODEL_ARTIFACT_PATH", str(tmp_root / "auction" / "baseline_v1.json"))
    get_settings.cache_clear()

    candidate = tmp_root / "resale" / "baseline_v1.json"
    _write_artifact(candidate, default_multiplier=1.5)
    v1 = promote_candidate_artifact("resale", candidate)

    _write_artifact(candidate, default_multiplier=2.0)
    v2 = promote_candidate_artifact("resale", candidate)

    switch_active_to_version("resale", str(v1["version_id"]))
    clear_model_artifact_cache()

    payload = {
        "brand": "Unknown",
        "line": None,
        "condition_grade": "B",
        "item_count_estimate": 1,
        "classification_confidence": 0.8,
        "classification_id": "unknown_fountain_pen",
    }
    first = predict_resale_value(_listing(), payload)

    switch_active_to_version("resale", str(v2["version_id"]))
    clear_model_artifact_cache()
    second = predict_resale_value(_listing(), payload)

    assert second["resale_pred_jpy"] > first["resale_pred_jpy"]
