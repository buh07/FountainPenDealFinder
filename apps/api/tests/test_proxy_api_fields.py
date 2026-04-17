from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.models import ProxyOptionEstimate, RawListing


client = TestClient(app)


def _seed_listing_with_proxy_rows(
    session,
    *,
    listing_id: str,
    source: str,
    title: str,
    options: list[dict],
    raw_attributes_json: str = "{}",
) -> None:
    session.add(
        RawListing(
            listing_id=listing_id,
            source=source,
            source_listing_id=f"src-{listing_id}",
            url=f"https://example.com/{listing_id}",
            title=title,
            description_raw="",
            images_json="[]",
            seller_id="seller",
            seller_rating=4.9,
            listing_format="buy_now",
            current_price_jpy=30000,
            price_buy_now_jpy=30000,
            domestic_shipping_jpy=1000,
            bid_count=None,
            listed_at=datetime.now(timezone.utc),
            ends_at=None,
            location_prefecture=None,
            condition_text=None,
            lot_size_hint=1,
            raw_attributes_json=raw_attributes_json,
        )
    )
    for payload in options:
        session.add(
            ProxyOptionEstimate(
                listing_id=listing_id,
                proxy_name=payload["proxy_name"],
                total_cost_jpy=payload["total_cost_jpy"],
                resale_reference_jpy=payload["resale_reference_jpy"],
                expected_profit_jpy=payload["expected_profit_jpy"],
                expected_profit_pct=payload["expected_profit_pct"],
                arbitrage_rank=payload.get("arbitrage_rank"),
                coupon_id=None,
                coupon_discount_jpy=0,
                cost_confidence=payload["cost_confidence"],
                is_recommended=payload.get("is_recommended", False),
            )
        )


def test_proxy_listing_endpoint_returns_risk_adjusted_and_recommendation_fields():
    init_db()
    listing_id = str(uuid4())
    with SessionLocal() as session:
        _seed_listing_with_proxy_rows(
            session,
            listing_id=listing_id,
            source="mercari",
            title="Pilot Custom 743",
            options=[
                {
                    "proxy_name": "Buyee",
                    "total_cost_jpy": 35000,
                    "resale_reference_jpy": 50000,
                    "expected_profit_jpy": 15000,
                    "expected_profit_pct": 0.42,
                    "arbitrage_rank": 1,
                    "cost_confidence": 0.72,
                    "is_recommended": True,
                },
                {
                    "proxy_name": "FromJapan",
                    "total_cost_jpy": 36000,
                    "resale_reference_jpy": 50000,
                    "expected_profit_jpy": 14000,
                    "expected_profit_pct": 0.39,
                    "arbitrage_rank": 2,
                    "cost_confidence": 0.95,
                    "is_recommended": False,
                },
            ],
            raw_attributes_json='{"proxy_first_time_user":["Buyee"]}',
        )
        session.commit()

    response = client.get(f"/proxy/listing/{listing_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_proxy_by_expected_profit"] == "Buyee"
    assert payload["best_proxy_by_risk_adjusted_cost"] == "FromJapan"
    assert payload["options"]
    first = payload["options"][0]
    assert "risk_adjusted_total_cost_jpy" in first
    assert "first_time_penalty_jpy" in first
    assert "is_recommended_by_risk_adjusted_cost" in first


def test_proxy_top_endpoint_marks_risk_adjusted_recommendation_per_listing():
    init_db()
    listing_a = str(uuid4())
    listing_b = str(uuid4())
    with SessionLocal() as session:
        _seed_listing_with_proxy_rows(
            session,
            listing_id=listing_a,
            source="mercari",
            title="Listing A",
            options=[
                {
                    "proxy_name": "Buyee",
                    "total_cost_jpy": 35000,
                    "resale_reference_jpy": 50000,
                    "expected_profit_jpy": 15000,
                    "expected_profit_pct": 0.42,
                    "arbitrage_rank": 1,
                    "cost_confidence": 0.72,
                    "is_recommended": True,
                },
                {
                    "proxy_name": "FromJapan",
                    "total_cost_jpy": 36000,
                    "resale_reference_jpy": 50000,
                    "expected_profit_jpy": 14000,
                    "expected_profit_pct": 0.39,
                    "arbitrage_rank": 2,
                    "cost_confidence": 0.95,
                    "is_recommended": False,
                },
            ],
        )
        _seed_listing_with_proxy_rows(
            session,
            listing_id=listing_b,
            source="rakuma",
            title="Listing B",
            options=[
                {
                    "proxy_name": "Buyee",
                    "total_cost_jpy": 43000,
                    "resale_reference_jpy": 70000,
                    "expected_profit_jpy": 27000,
                    "expected_profit_pct": 0.62,
                    "arbitrage_rank": 1,
                    "cost_confidence": 0.7,
                    "is_recommended": True,
                },
                {
                    "proxy_name": "Neokyo",
                    "total_cost_jpy": 42000,
                    "resale_reference_jpy": 70000,
                    "expected_profit_jpy": 28000,
                    "expected_profit_pct": 0.67,
                    "arbitrage_rank": 2,
                    "cost_confidence": 0.93,
                    "is_recommended": False,
                },
            ],
        )
        session.commit()

    response = client.get("/proxy/top?limit=20")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]

    grouped: dict[str, list[dict]] = {}
    for item in payload["items"]:
        grouped.setdefault(item["listing_id"], []).append(item)

    assert listing_a in grouped
    assert listing_b in grouped
    for listing_id, rows in grouped.items():
        flagged = [row for row in rows if row["is_recommended_by_risk_adjusted_cost"]]
        assert len(flagged) == 1, f"listing_id={listing_id} should have exactly one risk-adjusted recommendation"
