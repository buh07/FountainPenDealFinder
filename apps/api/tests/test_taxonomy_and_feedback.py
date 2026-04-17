import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.models import ClassificationResult, RawListing, TrainingExample
from app.services.taxonomy import canonicalize_condition_grade, resolve_taxonomy


client = TestClient(app)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _cleanup_feedback_files() -> None:
    settings = get_settings()
    root = _repo_root()

    taxonomy_path = Path(settings.taxonomy_feedback_types_path)
    if not taxonomy_path.is_absolute():
        taxonomy_path = root / taxonomy_path

    pricing_path = Path(settings.feedback_pricing_labels_path)
    if not pricing_path.is_absolute():
        pricing_path = root / pricing_path

    for path in [taxonomy_path, pricing_path]:
        if path.exists():
            path.unlink()


def _seed_listing_for_review(session) -> str:
    listing_id = str(uuid4())
    session.add(
        RawListing(
            listing_id=listing_id,
            source="mercari",
            source_listing_id=f"src-{listing_id}",
            url=f"https://example.com/{listing_id}",
            title="Pilot 912 fountain pen",
            description_raw="Used condition",
            images_json="[]",
            seller_id="tester",
            seller_rating=98.0,
            listing_format="buy_now",
            current_price_jpy=25000,
            price_buy_now_jpy=25000,
            domestic_shipping_jpy=800,
            bid_count=None,
            listed_at=None,
            ends_at=None,
            location_prefecture=None,
            condition_text="good",
            lot_size_hint=1,
            raw_attributes_json="{}",
        )
    )
    session.add(
        ClassificationResult(
            listing_id=listing_id,
            classification_id="pilot_fountain_pen",
            brand="Pilot",
            line=None,
            nib_material=None,
            nib_size=None,
            condition_grade="B",
            condition_flags_json="[]",
            item_count_estimate=1,
            items_json="[]",
            classification_confidence=0.8,
            condition_confidence=0.6,
            lot_decomposition_confidence=0.9,
            text_evidence="seed",
            image_evidence=None,
        )
    )
    session.commit()
    return listing_id


def test_taxonomy_standard_endpoint_returns_categories_and_conditions():
    response = client.get("/taxonomy/standard")
    assert response.status_code == 200

    payload = response.json()
    assert "categories" in payload
    assert "conditions" in payload
    assert "condition_taxonomy" in payload
    assert "damage_flag_taxonomy" in payload
    assert "types" in payload
    assert "B+" in payload["conditions"]
    assert any(item["brand"] == "Pilot" for item in payload["types"])
    assert "missing_converter" in payload["damage_flag_taxonomy"]
    assert len(payload["types"]) >= 20


def test_taxonomy_normalization_resolves_aliases_and_condition():
    resolved = resolve_taxonomy(text="モンブラン Meisterstuck 146 fountain pen")
    assert resolved["brand"] == "Montblanc"
    assert resolved["line"] == "146"
    assert resolved["classification_id"] == "montblanc_146"

    assert canonicalize_condition_grade("excellent") == "B+"
    assert canonicalize_condition_grade("junk") == "Parts/Repair"


def test_manual_feedback_adds_new_type_alias_and_pricing_feedback_row():
    init_db()
    _cleanup_feedback_files()

    with SessionLocal() as session:
        listing_id = _seed_listing_for_review(session)

    payload = {
        "action_type": "add_new_type",
        "corrected_brand": "Pilot",
        "corrected_line": "Custom 912",
        "taxonomy_aliases": ["912", "custom912"],
        "corrected_condition_grade": "excellent",
        "corrected_item_count": 1,
        "corrected_ask_price_jpy": 30000,
        "corrected_sold_price_jpy": 48000,
        "notes": "new type + price feedback",
        "reviewer": "self",
    }
    response = client.post(f"/review/{listing_id}", json=payload)
    assert response.status_code == 200

    with SessionLocal() as session:
        row = session.scalar(
            select(TrainingExample)
            .where(TrainingExample.listing_id == listing_id)
            .order_by(TrainingExample.created_at.desc())
            .limit(1)
        )
        assert row is not None
        labels = json.loads(row.label_json)
        assert labels["canonical_brand"] == "Pilot"
        assert labels["canonical_line"] == "Custom 912"
        assert labels["canonical_classification_id"] == "pilot_custom_912"
        assert labels["canonical_condition_grade"] == "B+"
        assert labels["corrected_sold_price_jpy"] == 48000

    settings = get_settings()
    root = _repo_root()

    taxonomy_path = Path(settings.taxonomy_feedback_types_path)
    if not taxonomy_path.is_absolute():
        taxonomy_path = root / taxonomy_path
    assert taxonomy_path.exists()
    assert "Custom 912" in taxonomy_path.read_text(encoding="utf-8")

    pricing_path = Path(settings.feedback_pricing_labels_path)
    if not pricing_path.is_absolute():
        pricing_path = root / pricing_path
    assert pricing_path.exists()
    pricing_rows = [json.loads(line) for line in pricing_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row.get("classification_id") == "pilot_custom_912" for row in pricing_rows)

    resolved = resolve_taxonomy(text="Pilot custom912 fountain pen")
    assert resolved["classification_id"] == "pilot_custom_912"
