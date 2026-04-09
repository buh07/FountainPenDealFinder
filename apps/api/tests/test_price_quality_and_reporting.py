import json
from datetime import date, datetime, timezone
from uuid import uuid4

from app.adapters.fixture_source import FixtureListingSourceAdapter
from app.adapters.mercari import MercariAdapter
from app.db import SessionLocal, init_db
from app.models import (
    AuctionPrediction,
    ClassificationResult,
    DealScore,
    ProxyOptionEstimate,
    RawListing,
    ValuationPrediction,
)
from app.services.pipeline import _prepare_listing_payload, score_single_listing, upsert_raw_listing
from app.services.reporting import get_listing_summary, list_ranked_listings


def _seed_scored_listing(
    session,
    *,
    listing_format: str,
    listed_at: datetime | None,
    ends_at: datetime | None,
    title: str,
    bucket: str = "potential",
) -> str:
    listing_id = str(uuid4())
    listing = RawListing(
        listing_id=listing_id,
        source="mercari" if listing_format != "auction" else "yahoo_auctions",
        source_listing_id=f"src-{listing_id}",
        url=f"https://example.com/{listing_id}",
        title=title,
        description_raw="",
        images_json="[]",
        seller_id="tester",
        seller_rating=99.0,
        listing_format=listing_format,
        current_price_jpy=12000,
        price_buy_now_jpy=(12000 if listing_format != "auction" else None),
        domestic_shipping_jpy=800,
        bid_count=(3 if listing_format == "auction" else None),
        listed_at=listed_at,
        ends_at=ends_at,
        location_prefecture=None,
        condition_text=None,
        lot_size_hint=1,
        raw_attributes_json="{}",
    )
    session.add(listing)
    session.flush()

    session.add(
        ClassificationResult(
            listing_id=listing_id,
            classification_id="pilot_custom_743",
            brand="Pilot",
            line="Custom 743",
            nib_material=None,
            nib_size=None,
            condition_grade="B+",
            condition_flags_json="[]",
            item_count_estimate=1,
            items_json="[]",
            classification_confidence=0.85,
            condition_confidence=0.8,
            lot_decomposition_confidence=0.9,
            text_evidence="seed",
            image_evidence=None,
        )
    )
    session.add(
        ValuationPrediction(
            listing_id=listing_id,
            resale_pred_jpy=35000,
            resale_ci_low_jpy=30000,
            resale_ci_high_jpy=40000,
            valuation_confidence=0.8,
        )
    )
    if listing_format == "auction" and ends_at is not None:
        session.add(
            AuctionPrediction(
                listing_id=listing_id,
                auction_low_win_price_jpy=12500,
                auction_expected_final_price_jpy=14000,
                auction_confidence=0.75,
            )
        )
    session.add(
        ProxyOptionEstimate(
            listing_id=listing_id,
            proxy_name="FromJapan",
            total_cost_jpy=15000,
            resale_reference_jpy=35000,
            expected_profit_jpy=20000,
            expected_profit_pct=1.3333,
            arbitrage_rank=1,
            coupon_id=None,
            coupon_discount_jpy=0,
            cost_confidence=0.8,
            is_recommended=True,
        )
    )
    session.add(
        DealScore(
            listing_id=listing_id,
            expected_profit_jpy=20000,
            expected_profit_pct=1.3333,
            risk_adjusted_profit_jpy=15000,
            confidence_overall=0.75,
            bucket=bucket,
            risk_flags_json="[]",
            rationale="seeded",
        )
    )
    session.flush()
    return listing_id


def test_missing_price_forces_discard_and_summary_status():
    init_db()
    with SessionLocal() as session:
        payload = {
            "source": "mercari",
            "source_listing_id": f"missing-{uuid4().hex}",
            "url": "https://example.com/missing",
            "title": "Pilot Custom 743",
            "description_raw": "No visible price",
            "images": [],
            "listing_format": "buy_now",
            "current_price_jpy": 0,
            "price_buy_now_jpy": 0,
            "domestic_shipping_jpy": 1000,
            "raw_attributes": {},
        }
        listing = upsert_raw_listing(session, payload)
        artifacts = score_single_listing(session, listing)
        session.commit()

        score_row = artifacts["deal_score"]
        risk_flags = json.loads(score_row.risk_flags_json)
        assert score_row.bucket == "discard"
        assert score_row.expected_profit_jpy == 0
        assert score_row.expected_profit_pct == 0.0
        assert "price_missing" in risk_flags

        summary = get_listing_summary(session, listing.listing_id)
        assert summary is not None
        assert summary.price_status == "missing"
        assert "price_missing" in summary.risk_flags


def test_parse_error_unresolved_is_low_conf_potential_with_neutral_profit():
    init_db()
    with SessionLocal() as session:
        payload = {
            "source": "mercari",
            "source_listing_id": f"parse-error-{uuid4().hex}",
            "url": "https://example.com/parse-error",
            "title": "Sailor 1911L",
            "description_raw": "価格: --",
            "images": [],
            "listing_format": "buy_now",
            "current_price_jpy": 0,
            "price_buy_now_jpy": 0,
            "domestic_shipping_jpy": 900,
            "raw_attributes": {"price_parse_error": True},
        }
        listing = upsert_raw_listing(session, payload)
        artifacts = score_single_listing(session, listing)
        session.commit()

        score_row = artifacts["deal_score"]
        risk_flags = json.loads(score_row.risk_flags_json)
        assert score_row.bucket == "potential"
        assert score_row.expected_profit_jpy == 0
        assert score_row.expected_profit_pct == 0.0
        assert "price_parse_error" in risk_flags
        assert "needs_manual_price_review" in risk_flags

        summary = get_listing_summary(session, listing.listing_id)
        assert summary is not None
        assert summary.price_status == "parse_error"
        assert "needs_manual_price_review" in summary.risk_flags


def test_parse_error_payload_repairs_with_detail_fetch(monkeypatch):
    def _fake_detail(self, source_id: str):  # noqa: ARG001
        return {
            "source": "mercari",
            "source_listing_id": source_id,
            "url": f"https://example.com/{source_id}",
            "title": "Repaired",
            "listing_format": "buy_now",
            "current_price_jpy": 21000,
            "price_buy_now_jpy": 21000,
            "raw_attributes": {},
        }

    monkeypatch.setattr(MercariAdapter, "fetch_listing_detail", _fake_detail)

    payload = {
        "source": "mercari",
        "source_listing_id": "repair-1",
        "url": "https://example.com/repair-1",
        "title": "No parseable price",
        "description_raw": "価格: --",
        "listing_format": "buy_now",
        "current_price_jpy": 0,
        "price_buy_now_jpy": 0,
        "raw_attributes": {"price_parse_error": True},
    }
    prepared = _prepare_listing_payload(payload)

    assert prepared["current_price_jpy"] == 21000
    assert prepared["price_buy_now_jpy"] == 21000
    assert prepared["raw_attributes"].get("price_parse_error") is None
    assert prepared["raw_attributes"].get("price_repaired_from") == "detail_fetch"


def test_parse_error_detail_repair_failure_is_logged(monkeypatch, caplog):
    def _raise_detail(self, source_id: str):  # noqa: ARG001
        raise RuntimeError("blocked")

    monkeypatch.setattr(MercariAdapter, "fetch_listing_detail", _raise_detail)
    payload = {
        "source": "mercari",
        "source_listing_id": "repair-fail-1",
        "url": "https://example.com/repair-fail-1",
        "title": "No parseable price",
        "description_raw": "価格: --",
        "listing_format": "buy_now",
        "current_price_jpy": 0,
        "price_buy_now_jpy": 0,
        "raw_attributes": {"price_parse_error": True},
    }

    with caplog.at_level("ERROR"):
        prepared = _prepare_listing_payload(payload)

    assert prepared["raw_attributes"].get("price_parse_error") is True
    assert any("Detail price repair fetch failed" in record.message for record in caplog.records)


def test_fixture_stale_fallback_marks_rows():
    adapter = FixtureListingSourceAdapter()
    far_future = datetime(2030, 1, 1, tzinfo=timezone.utc)

    rows = adapter.get_fresh_window_listings(
        window_start=far_future,
        category="fountain_pen",
        source_filter="mercari",
    )
    assert rows
    assert all(row.get("raw_attributes", {}).get("fixture_stale_fallback") for row in rows)

    ending_rows = adapter.get_ending_auctions(
        window_start=far_future,
        window_end=far_future,
        category="fountain_pen",
        source_filter="yahoo_auctions",
    )
    assert ending_rows
    assert all(row.get("raw_attributes", {}).get("fixture_stale_fallback") for row in ending_rows)


def test_report_window_filters_fixed_by_day_and_auctions_by_rolling_24h():
    init_db()
    generated_at = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    report_date = date(2026, 4, 8)

    with SessionLocal() as session:
        fixed_in_day = _seed_scored_listing(
            session,
            listing_format="buy_now",
            listed_at=datetime(2026, 4, 8, 1, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="Fixed In Day",
        )
        fixed_out_of_day = _seed_scored_listing(
            session,
            listing_format="buy_now",
            listed_at=datetime(2026, 4, 6, 1, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="Fixed Out Of Day",
        )
        auction_in_window = _seed_scored_listing(
            session,
            listing_format="auction",
            listed_at=datetime(2026, 4, 8, 2, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 8, 14, 0, tzinfo=timezone.utc),
            title="Auction In Window",
        )
        auction_unknown_end = _seed_scored_listing(
            session,
            listing_format="auction",
            listed_at=datetime(2026, 4, 8, 2, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="Auction Unknown End",
        )
        auction_out_of_window = _seed_scored_listing(
            session,
            listing_format="auction",
            listed_at=datetime(2026, 4, 8, 2, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 9, 20, 0, tzinfo=timezone.utc),
            title="Auction Out Of Window",
        )
        auction_on_boundary = _seed_scored_listing(
            session,
            listing_format="auction",
            listed_at=datetime(2026, 4, 8, 2, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc),
            title="Auction On Boundary",
        )
        session.commit()

        items = list_ranked_listings(
            session,
            bucket="potential",
            limit=50,
            report_date=report_date,
            generated_at=generated_at,
        )
        listing_ids = {item.listing_id for item in items}

        assert fixed_in_day in listing_ids
        assert auction_in_window in listing_ids
        assert fixed_out_of_day not in listing_ids
        assert auction_unknown_end not in listing_ids
        assert auction_out_of_window not in listing_ids
        assert auction_on_boundary not in listing_ids


def test_list_ranked_listings_supports_offset_pagination():
    init_db()
    with SessionLocal() as session:
        first = _seed_scored_listing(
            session,
            listing_format="buy_now",
            listed_at=datetime(2026, 4, 8, 1, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="First Ranked",
        )
        second = _seed_scored_listing(
            session,
            listing_format="buy_now",
            listed_at=datetime(2026, 4, 8, 2, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="Second Ranked",
        )
        third = _seed_scored_listing(
            session,
            listing_format="buy_now",
            listed_at=datetime(2026, 4, 8, 3, 0, tzinfo=timezone.utc),
            ends_at=None,
            title="Third Ranked",
        )

        score_rows = session.query(DealScore).filter(DealScore.listing_id.in_([first, second, third])).all()
        for row in score_rows:
            if row.listing_id == first:
                row.risk_adjusted_profit_jpy = 30000
            elif row.listing_id == second:
                row.risk_adjusted_profit_jpy = 20000
            else:
                row.risk_adjusted_profit_jpy = 10000
            session.add(row)
        session.commit()

        page_1 = list_ranked_listings(
            session,
            bucket="potential",
            limit=1,
            offset=0,
        )
        page_2 = list_ranked_listings(
            session,
            bucket="potential",
            limit=1,
            offset=1,
        )

        assert len(page_1) == 1
        assert len(page_2) == 1
        assert page_1[0].listing_id != page_2[0].listing_id
