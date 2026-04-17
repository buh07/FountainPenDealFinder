from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.models import ListingAsset
from app.models import DealScore, RawListing


client = TestClient(app)


def _seed_listing(
    session,
    source: str,
    rank: int,
    *,
    expected_profit_jpy: int = 5000,
    expected_profit_pct: float = 0.5,
    listing_format: str = "buy_now",
    ends_at: datetime | None = None,
) -> str:
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
            listing_format=listing_format,
            current_price_jpy=10000,
            price_buy_now_jpy=(10000 if listing_format == "buy_now" else None),
            domestic_shipping_jpy=800,
            bid_count=None,
            listed_at=datetime.now(timezone.utc),
            ends_at=ends_at,
            location_prefecture=None,
            condition_text=None,
            lot_size_hint=1,
            raw_attributes_json="{}",
        )
    )
    session.add(
        DealScore(
            listing_id=listing_id,
            expected_profit_jpy=expected_profit_jpy,
            expected_profit_pct=expected_profit_pct,
            risk_adjusted_profit_jpy=1000 * rank,
            confidence_overall=0.7,
            bucket="potential",
            risk_flags_json="[]",
            rationale="seed",
        )
    )
    return listing_id


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


def test_listings_endpoint_supports_profit_sort_views():
    init_db()
    source = f"sort_source_{uuid4().hex}"

    with SessionLocal() as session:
        low_flat_high_pct = _seed_listing(
            session,
            source,
            rank=1,
            expected_profit_jpy=1000,
            expected_profit_pct=0.8,
        )
        high_flat_low_pct = _seed_listing(
            session,
            source,
            rank=2,
            expected_profit_jpy=9000,
            expected_profit_pct=0.2,
        )
        session.commit()

    flat_resp = client.get(f"/listings?source={source}&bucket=potential&limit=2&sort_by=flat_profit")
    assert flat_resp.status_code == 200
    flat_ids = [item["listing_id"] for item in flat_resp.json()["items"]]
    assert flat_ids[0] == high_flat_low_pct

    pct_resp = client.get(f"/listings?source={source}&bucket=potential&limit=2&sort_by=percent_profit")
    assert pct_resp.status_code == 200
    pct_ids = [item["listing_id"] for item in pct_resp.json()["items"]]
    assert pct_ids[0] == low_flat_high_pct


def test_listings_endpoint_supports_auction_window_filter():
    init_db()
    source = f"ending_source_{uuid4().hex}"
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        in_window = _seed_listing(
            session,
            source,
            rank=2,
            listing_format="auction",
            ends_at=now + timedelta(hours=1),
        )
        _seed_listing(
            session,
            source,
            rank=1,
            listing_format="auction",
            ends_at=now + timedelta(hours=10),
        )
        session.commit()

    response = client.get(
        f"/listings?source={source}&bucket=potential&listing_type=auction&ending_within_hours=2&limit=10"
    )
    assert response.status_code == 200
    payload = response.json()
    ids = {item["listing_id"] for item in payload["items"]}
    assert in_window in ids
    assert len(ids) == 1


def test_listing_images_endpoint_returns_image_urls_and_assets():
    init_db()
    source = f"image_source_{uuid4().hex}"

    with SessionLocal() as session:
        listing_id = str(uuid4())
        session.add(
            RawListing(
                listing_id=listing_id,
                source=source,
                source_listing_id=f"{source}-{listing_id}",
                url=f"https://example.com/{listing_id}",
                title="Image Listing",
                description_raw="",
                images_json='["https://cdn.example.com/a.jpg","https://cdn.example.com/b.jpg"]',
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
                risk_adjusted_profit_jpy=1000,
                confidence_overall=0.7,
                bucket="potential",
                risk_flags_json="[]",
                rationale="seed",
            )
        )
        session.add(
            ListingAsset(
                listing_id=listing_id,
                asset_type="image",
                local_path="data/object_store/mercari/123/image/hash.jpg",
                source_url="https://cdn.example.com/a.jpg",
                content_hash="hash",
            )
        )
        session.commit()

    response = client.get(f"/listings/{listing_id}/images")
    assert response.status_code == 200
    payload = response.json()
    assert payload["listing_id"] == listing_id
    assert len(payload["image_urls"]) == 2
    assert payload["captured_assets"]
