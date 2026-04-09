from app.core.config import get_settings
from app.models import RawListing
from app.services.classification_pipeline import classify_listing_multi_stage


def _listing(*, title: str, condition_text: str | None = None, images_json: str = "[]") -> RawListing:
    return RawListing(
        source="mercari",
        source_listing_id="seed-1",
        url="https://example.com/listing/1",
        title=title,
        description_raw="",
        images_json=images_json,
        seller_id="seller-1",
        seller_rating=4.9,
        listing_format="buy_now",
        current_price_jpy=12000,
        price_buy_now_jpy=12000,
        domestic_shipping_jpy=800,
        bid_count=None,
        listed_at=None,
        ends_at=None,
        location_prefecture=None,
        condition_text=condition_text,
        lot_size_hint=1,
        raw_attributes_json="{}",
    )


def test_image_stage_can_disambiguate_line_when_text_is_ambiguous(monkeypatch):
    monkeypatch.setenv("IMAGE_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("IMAGE_EMBEDDING_MODEL_NAME", "local-hash-v1")
    get_settings.cache_clear()

    listing = _listing(
        title="Pilot Custom fountain pen",
        images_json='["https://cdn.example.com/pilot_custom_743_front.jpg"]',
    )

    payload = classify_listing_multi_stage(listing)

    assert payload["brand"] == "Pilot"
    assert payload["line"] == "Custom 743"
    assert payload["classification_id"] == "pilot_custom_743"
    assert payload["image_evidence"] is not None
    assert "image_evidence_unavailable" not in payload["uncertainty_tags"]


def test_text_only_fallback_when_image_stage_disabled(monkeypatch):
    monkeypatch.setenv("IMAGE_CLASSIFIER_ENABLED", "false")
    get_settings.cache_clear()

    listing = _listing(
        title="Pilot Custom fountain pen",
        images_json='["https://cdn.example.com/pilot_custom_743_front.jpg"]',
    )

    payload = classify_listing_multi_stage(listing)

    assert payload["brand"] == "Pilot"
    assert payload["image_evidence"] is None
    assert "image_evidence_unavailable" in payload["uncertainty_tags"]


def test_condition_stage_normalizes_to_canonical_grades(monkeypatch):
    monkeypatch.setenv("IMAGE_CLASSIFIER_ENABLED", "false")
    get_settings.cache_clear()

    listing = _listing(
        title="Pilot custom",
        condition_text="junk repair nib cracked",
    )

    payload = classify_listing_multi_stage(listing)

    assert payload["condition_grade"] == "Parts/Repair"
    assert "condition_risk_high" in payload["uncertainty_tags"]


def test_low_confidence_image_signal_is_flagged_without_confidence_blend(monkeypatch):
    monkeypatch.setenv("IMAGE_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("IMAGE_CLASSIFIER_BLEND_MIN_CONFIDENCE", "0.8")
    get_settings.cache_clear()

    listing = _listing(
        title="Vintage fountain pen",
        images_json='["https://cdn.example.com/pilot.jpg"]',
    )

    payload = classify_listing_multi_stage(listing)
    assert payload["image_evidence"] is not None
    assert "image_evidence_low_confidence" in payload["uncertainty_tags"]
    get_settings.cache_clear()
