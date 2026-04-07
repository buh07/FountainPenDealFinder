from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.adapters.mercari import MercariAdapter
from app.adapters.rakuma import RakumaAdapter
from app.adapters.yahoo_auctions import YahooAuctionsAdapter
from app.adapters.yahoo_flea_market import YahooFleaMarketAdapter
from app.services.pipeline import _collect_with_retries, _filter_parse_complete_rows


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_yahoo_auctions_ldjson_parser_extracts_required_fields(monkeypatch):
    adapter = YahooAuctionsAdapter()
    html = _read_fixture("yahoo_auctions_ldjson.html")
    monkeypatch.setattr(adapter, "_request", lambda path, params=None: html)

    rows = adapter.get_fresh_window_listings(
        window_start=datetime.now(timezone.utc) - timedelta(days=1),
        category="fountain_pen",
    )

    assert rows
    row = rows[0]
    assert row["source"] == "yahoo_auctions"
    assert row["source_listing_id"] == "abc12345"
    assert row["url"].startswith("https://")
    assert row["title"]
    assert row["current_price_jpy"] > 0


def test_yahoo_flea_market_parser_extracts_required_fields(monkeypatch):
    adapter = YahooFleaMarketAdapter()
    html = _read_fixture("yahoo_flea_market_search.html")
    monkeypatch.setattr(adapter, "_request", lambda path, params=None: html)

    rows = adapter.get_fresh_window_listings(
        window_start=datetime.now(timezone.utc) - timedelta(days=1),
        category="fountain_pen",
    )

    assert rows
    row = rows[0]
    assert row["source"] == "yahoo_flea_market"
    assert row["source_listing_id"] == "yf-4004"
    assert row["current_price_jpy"] == 98000
    assert row["title"]


def test_mercari_parser_extracts_required_fields(monkeypatch):
    adapter = MercariAdapter()
    html = _read_fixture("mercari_search.html")
    monkeypatch.setattr(adapter, "_request", lambda path, params=None: html)

    rows = adapter.get_fresh_window_listings(
        window_start=datetime.now(timezone.utc) - timedelta(days=1),
        category="fountain_pen",
    )

    assert rows
    row = rows[0]
    assert row["source"] == "mercari"
    assert row["source_listing_id"] == "m1234567890"
    assert row["current_price_jpy"] == 28000
    assert row["title"]


def test_rakuma_parser_extracts_required_fields(monkeypatch):
    adapter = RakumaAdapter()
    html = _read_fixture("rakuma_search.html")
    monkeypatch.setattr(adapter, "_request", lambda path, params=None: html)

    rows = adapter.get_fresh_window_listings(
        window_start=datetime.now(timezone.utc) - timedelta(days=1),
        category="fountain_pen",
    )

    assert rows
    row = rows[0]
    assert row["source"] == "rakuma"
    assert row["source_listing_id"] == "rk-3003"
    assert row["current_price_jpy"] == 17000
    assert row["title"]


def test_parse_completeness_filter_keeps_only_complete_rows():
    rows = [
        {
            "source": "mercari",
            "source_listing_id": "ok-1",
            "url": "https://example.com/ok-1",
            "title": "Some pen",
            "listing_format": "buy_now",
            "current_price_jpy": 10000,
        },
        {
            "source": "mercari",
            "source_listing_id": "bad-1",
            "url": "",
            "title": "",
            "listing_format": "",
            "current_price_jpy": None,
            "price_buy_now_jpy": None,
        },
    ]

    filtered = _filter_parse_complete_rows(rows, min_completeness=0.55)
    assert len(filtered) == 1
    assert filtered[0]["source_listing_id"] == "ok-1"


def test_collect_with_retries_recovers_from_transient_failure():
    calls = {"count": 0}

    def flaky_fetch():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient failure")
        return [
            {
                "source": "yahoo_auctions",
                "source_listing_id": "a-1",
                "url": "https://example.com/a-1",
                "title": "Pilot Custom",
                "listing_format": "auction",
                "current_price_jpy": 12000,
            }
        ]

    rows = _collect_with_retries(
        fetch_fn=flaky_fetch,
        attempts=3,
        backoff_seconds=0.0,
        min_completeness=0.55,
        min_valid_rows=1,
    )

    assert calls["count"] == 2
    assert len(rows) == 1
    assert rows[0]["source_listing_id"] == "a-1"
