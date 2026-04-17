from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ClassificationResult, DealScore, ManualReview, RawListing
from app.services.confidence_calibration import (
    calibrate_classification_confidence,
    reset_confidence_calibration_cache,
)


def _seed_review_sample(session, *, listing_id: str, confidence: float, is_correct: bool, created_at: datetime) -> None:
    session.add(
        RawListing(
            listing_id=listing_id,
            source="mercari",
            source_listing_id=f"src-{listing_id}",
            url=f"https://example.com/{listing_id}",
            title="Calibration listing",
            description_raw="",
            images_json="[]",
            seller_id="seed",
            seller_rating=5.0,
            listing_format="buy_now",
            current_price_jpy=10000,
            price_buy_now_jpy=10000,
            domestic_shipping_jpy=800,
            bid_count=None,
            listed_at=created_at,
            ends_at=None,
            location_prefecture=None,
            condition_text=None,
            lot_size_hint=1,
            raw_attributes_json="{}",
        )
    )
    session.add(
        ClassificationResult(
            listing_id=listing_id,
            classification_id="pilot_custom_743",
            brand="Pilot",
            line="Custom 743",
            nib_material=None,
            nib_size=None,
            condition_grade="B",
            condition_flags_json="[]",
            item_count_estimate=1,
            items_json="[]",
            classification_confidence=confidence,
            condition_confidence=0.6,
            lot_decomposition_confidence=0.9,
            text_evidence="seed",
            image_evidence=None,
        )
    )
    session.add(
        ManualReview(
            listing_id=listing_id,
            action_type=("confirm_classification" if is_correct else "correct_classification"),
            corrected_classification_id=None,
            corrected_condition_grade=None,
            is_false_positive=False,
            was_purchased=False,
            notes="",
            reviewer="tester",
            created_at=created_at,
        )
    )


def _clear_tables(session) -> None:
    session.query(ManualReview).delete()
    session.query(ClassificationResult).delete()
    session.query(DealScore).delete()
    session.query(RawListing).delete()
    session.commit()


def test_calibration_falls_back_to_raw_when_labels_insufficient(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_CALIBRATION_MIN_ROWS", "10")
    monkeypatch.setenv("CLASSIFICATION_CALIBRATION_BIN_COUNT", "4")
    get_settings.cache_clear()
    reset_confidence_calibration_cache()

    init_db()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        _clear_tables(session)
        _seed_review_sample(
            session,
            listing_id="cal-insufficient-1",
            confidence=0.9,
            is_correct=True,
            created_at=now - timedelta(minutes=2),
        )
        _seed_review_sample(
            session,
            listing_id="cal-insufficient-2",
            confidence=0.2,
            is_correct=False,
            created_at=now - timedelta(minutes=1),
        )
        session.commit()

        calibrated, info = calibrate_classification_confidence(session, 0.73)

    assert round(calibrated, 4) == 0.73
    assert info["applied"] is False
    assert info["sample_count"] == 2
    get_settings.cache_clear()
    reset_confidence_calibration_cache()


def test_calibration_applies_monotonic_mapping_when_labels_sufficient(monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_CALIBRATION_MIN_ROWS", "5")
    monkeypatch.setenv("CLASSIFICATION_CALIBRATION_BIN_COUNT", "5")
    get_settings.cache_clear()
    reset_confidence_calibration_cache()

    init_db()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        _clear_tables(session)
        _seed_review_sample(
            session,
            listing_id="cal-sufficient-1",
            confidence=0.2,
            is_correct=True,
            created_at=now - timedelta(minutes=4),
        )
        _seed_review_sample(
            session,
            listing_id="cal-sufficient-2",
            confidence=0.4,
            is_correct=False,
            created_at=now - timedelta(minutes=3),
        )
        _seed_review_sample(
            session,
            listing_id="cal-sufficient-3",
            confidence=0.6,
            is_correct=True,
            created_at=now - timedelta(minutes=2),
        )
        _seed_review_sample(
            session,
            listing_id="cal-sufficient-4",
            confidence=0.9,
            is_correct=False,
            created_at=now - timedelta(minutes=1),
        )
        _seed_review_sample(
            session,
            listing_id="cal-sufficient-5",
            confidence=0.75,
            is_correct=True,
            created_at=now - timedelta(seconds=30),
        )
        session.commit()

        calibrated_low, info_low = calibrate_classification_confidence(session, 0.2)
        calibrated_high, info_high = calibrate_classification_confidence(session, 0.9)

    assert info_low["applied"] is True
    assert info_high["applied"] is True
    assert info_low["sample_count"] >= 5
    assert 0.0 <= calibrated_low <= calibrated_high <= 1.0
    get_settings.cache_clear()
    reset_confidence_calibration_cache()
