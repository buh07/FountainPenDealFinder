from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.models import ClassificationResult, DealScore, RawListing, ValuationPrediction
from app.services.pipeline import run_priority_auction_refresh, select_priority_auction_candidates
from app.adapters.yahoo_auctions import YahooAuctionsAdapter


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apps.worker import worker


client = TestClient(app)


def _seed_auction(
    session,
    *,
    hours_to_end: float,
    profit_pct: float,
    confidence: float,
    expected_profit_jpy: int = 15000,
    classification_id: str = "pilot_custom_743",
    brand: str = "Pilot",
    resale_pred_jpy: int = 60000,
) -> str:
    listing_id = str(uuid4())
    now = datetime.now(timezone.utc)
    session.add(
        RawListing(
            listing_id=listing_id,
            source="yahoo_auctions",
            source_listing_id=f"src-{listing_id}",
            url=f"https://example.com/{listing_id}",
            title="Auction listing",
            description_raw="",
            images_json="[]",
            seller_id="seller-1",
            seller_rating=4.9,
            listing_format="auction",
            current_price_jpy=10000,
            price_buy_now_jpy=None,
            domestic_shipping_jpy=800,
            bid_count=3,
            listed_at=now - timedelta(hours=2),
            ends_at=now + timedelta(hours=hours_to_end),
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
            expected_profit_pct=profit_pct,
            risk_adjusted_profit_jpy=int(expected_profit_jpy * confidence),
            confidence_overall=confidence,
            bucket="potential",
            risk_flags_json="[]",
            rationale="seed",
        )
    )
    session.add(
        ClassificationResult(
            listing_id=listing_id,
            classification_id=classification_id,
            brand=brand,
            line=classification_id.split("_", 1)[-1],
            nib_material=None,
            nib_size=None,
            condition_grade="B",
            condition_flags_json="[]",
            item_count_estimate=1,
            items_json="[]",
            classification_confidence=confidence,
            condition_confidence=0.7,
            lot_decomposition_confidence=0.9,
            text_evidence="seed",
            image_evidence=None,
        )
    )
    session.add(
        ValuationPrediction(
            listing_id=listing_id,
            resale_pred_jpy=resale_pred_jpy,
            resale_ci_low_jpy=max(0, resale_pred_jpy - 5000),
            resale_ci_high_jpy=resale_pred_jpy + 5000,
            valuation_confidence=0.8,
        )
    )
    return listing_id


def test_select_priority_auction_candidates_filters_by_score_and_window():
    init_db()
    with SessionLocal() as session:
        session.query(DealScore).delete()
        session.query(RawListing).delete()
        session.commit()

        high_id = _seed_auction(session, hours_to_end=0.5, profit_pct=0.9, confidence=0.9)
        _seed_auction(session, hours_to_end=3.0, profit_pct=0.9, confidence=0.9)  # outside 2h window
        _seed_auction(session, hours_to_end=1.0, profit_pct=0.05, confidence=0.2)  # low score
        session.commit()

        selected = select_priority_auction_candidates(
            session,
            window_hours=2,
            threshold=0.55,
            limit=10,
        )

    selected_ids = {listing.listing_id for listing, _score in selected}
    assert high_id in selected_ids
    assert len(selected_ids) == 1


def test_worker_scheduler_runs_priority_tier(monkeypatch):
    calls: list[str] = []
    stop_event = Event()

    monkeypatch.setattr(worker, "run_once", lambda report_date=None: calls.append("fixed"))
    monkeypatch.setattr(worker, "run_ending_refresh_once", lambda window_hours: calls.append("ending"))

    def _priority(window_hours: int, threshold: float) -> None:
        calls.append(f"priority:{window_hours}:{threshold:.2f}")
        stop_event.set()

    monkeypatch.setattr(worker, "run_priority_refresh_once", _priority)

    worker.run_scheduler_loop(
        fixed_interval_seconds=60,
        ending_interval_seconds=60,
        priority_interval_seconds=60,
        idle_sleep_seconds=1,
        ending_window_hours=24,
        priority_window_hours=2,
        priority_threshold=0.55,
        stop_event=stop_event,
    )

    assert calls == ["priority:2:0.55"]


def test_refresh_priority_endpoint_returns_expected_shape(monkeypatch):
    from app.routers import collect as collect_router

    monkeypatch.setattr(
        collect_router,
        "run_priority_auction_refresh",
        lambda db, window_hours, threshold: {
            "candidate_count": 3,
            "ingested_count": 2,
            "scored_count": 2,
            "window_hours": window_hours,
            "threshold": threshold,
        },
    )

    response = client.post("/collect/refresh-priority?window_hours=2&threshold=0.55")
    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_count"] == 3
    assert payload["ingested_count"] == 2
    assert payload["scored_count"] == 2
    assert payload["window_hours"] == 2


def test_priority_refresh_logs_traceback_on_detail_fetch_exception(monkeypatch, caplog):
    init_db()
    with SessionLocal() as session:
        session.query(DealScore).delete()
        session.query(RawListing).delete()
        session.commit()
        _seed_auction(session, hours_to_end=1.0, profit_pct=0.9, confidence=0.9)
        session.commit()

        def _raise(self, source_listing_id: str):  # noqa: ARG001
            raise RuntimeError("priority-fetch-blocked")

        monkeypatch.setattr(YahooAuctionsAdapter, "fetch_listing_detail", _raise)
        with caplog.at_level("ERROR"):
            result = run_priority_auction_refresh(
                session,
                window_hours=2,
                threshold=0.0,
                limit=10,
            )

    assert result["candidate_count"] >= 1
    assert result["ingested_count"] == 0
    assert any("Priority detail fetch failed" in record.message for record in caplog.records)


def test_priority_candidates_use_value_and_rarity_factors():
    init_db()
    with SessionLocal() as session:
        session.query(ValuationPrediction).delete()
        session.query(ClassificationResult).delete()
        session.query(DealScore).delete()
        session.query(RawListing).delete()
        session.commit()

        rare_high_value = _seed_auction(
            session,
            hours_to_end=1.4,
            profit_pct=0.25,
            confidence=0.55,
            expected_profit_jpy=9000,
            classification_id="namiki_emperor",
            brand="Namiki",
            resale_pred_jpy=260000,
        )
        _seed_auction(
            session,
            hours_to_end=1.4,
            profit_pct=0.25,
            confidence=0.55,
            expected_profit_jpy=9000,
            classification_id="pilot_custom_743",
            brand="Pilot",
            resale_pred_jpy=60000,
        )
        _seed_auction(
            session,
            hours_to_end=1.4,
            profit_pct=0.25,
            confidence=0.55,
            expected_profit_jpy=9000,
            classification_id="pilot_custom_743",
            brand="Pilot",
            resale_pred_jpy=62000,
        )
        session.commit()

        selected = select_priority_auction_candidates(
            session,
            window_hours=2,
            threshold=0.0,
            limit=3,
        )

    assert selected
    assert selected[0][0].listing_id == rare_high_value


def test_priority_value_signal_uses_configurable_ceiling(monkeypatch):
    init_db()
    with SessionLocal() as session:
        session.query(ValuationPrediction).delete()
        session.query(ClassificationResult).delete()
        session.query(DealScore).delete()
        session.query(RawListing).delete()
        session.commit()

        seeded_id = _seed_auction(
            session,
            hours_to_end=1.0,
            profit_pct=0.0,
            confidence=0.0,
            expected_profit_jpy=0,
            classification_id="pilot_custom_823",
            brand="Pilot",
            resale_pred_jpy=20000,
        )
        session.commit()

        monkeypatch.setenv("PRIORITY_VALUE_REFERENCE_JPY_CEILING", "200000")
        get_settings.cache_clear()
        high_ceiling_selected = select_priority_auction_candidates(
            session,
            window_hours=2,
            threshold=0.0,
            limit=5,
        )
        assert high_ceiling_selected
        high_ceiling_score = next(score for listing, score in high_ceiling_selected if listing.listing_id == seeded_id)

        monkeypatch.setenv("PRIORITY_VALUE_REFERENCE_JPY_CEILING", "10000")
        get_settings.cache_clear()
        low_ceiling_selected = select_priority_auction_candidates(
            session,
            window_hours=2,
            threshold=0.0,
            limit=5,
        )
        assert low_ceiling_selected
        low_ceiling_score = next(score for listing, score in low_ceiling_selected if listing.listing_id == seeded_id)

    assert low_ceiling_score > high_ceiling_score
    get_settings.cache_clear()
