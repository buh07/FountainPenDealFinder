from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.models import DealScore, RawListing


client = TestClient(app)


def _seed_listing(session, source: str, rank: int) -> None:
    listing_id = str(uuid4())
    session.add(
        RawListing(
            listing_id=listing_id,
            source=source,
            source_listing_id=f"{source}-{listing_id}",
            url=f"https://example.com/{listing_id}",
            title=f"Listing {rank}",
            description_raw="",
            images_json="[]",
            seller_id="tester",
            seller_rating=99.0,
            listing_format="buy_now",
            current_price_jpy=10000,
            price_buy_now_jpy=10000,
            domestic_shipping_jpy=800,
            bid_count=None,
            listed_at=datetime.now(timezone.utc),
            ends_at=None,
            location_prefecture=None,
            condition_text=None,
            lot_size_hint=1,
            raw_attributes_json="{}",
        )
    )
    session.add(
        DealScore(
            listing_id=listing_id,
            expected_profit_jpy=5000,
            expected_profit_pct=0.5,
            risk_adjusted_profit_jpy=1000 * rank,
            confidence_overall=0.7,
            bucket="potential",
            risk_flags_json="[]",
            rationale="seed",
        )
    )


def test_listings_endpoint_supports_limit_and_offset():
    init_db()
    source = f"pagination_source_{uuid4().hex}"

    with SessionLocal() as session:
        _seed_listing(session, source, rank=3)
        _seed_listing(session, source, rank=2)
        _seed_listing(session, source, rank=1)
        session.commit()

    response = client.get(f"/listings?source={source}&bucket=potential&limit=1&offset=1")
    assert response.status_code == 200

    payload = response.json()
    assert payload["total"] >= 3
    assert len(payload["items"]) == 1
