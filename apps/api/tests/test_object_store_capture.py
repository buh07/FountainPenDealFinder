from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ListingAsset, RawListing
from app.services.object_store import capture_listing_assets


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401, ARG002
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    def get(self, url: str) -> _Response:
        return _Response(content=f"binary:{url}".encode("utf-8"))


def _seed_listing(session) -> RawListing:
    listing = RawListing(
        listing_id=str(uuid4()),
        source="mercari",
        source_listing_id=f"src-{uuid4().hex}",
        url="https://example.com/listing/1",
        title="Pilot Custom",
        description_raw="<html><body>sample listing body</body></html>",
        images_json='["https://cdn.example.com/images/pen_1.jpg"]',
        seller_id="seller-1",
        seller_rating=4.9,
        listing_format="buy_now",
        current_price_jpy=12000,
        price_buy_now_jpy=12000,
        domestic_shipping_jpy=700,
        bid_count=None,
        listed_at=None,
        ends_at=None,
        location_prefecture=None,
        condition_text=None,
        lot_size_hint=1,
        raw_attributes_json="{}",
    )
    session.add(listing)
    session.flush()
    return listing


def test_capture_listing_assets_writes_files_and_dedupes(monkeypatch):
    tmp_root = Path("/tmp/fpdf_test_object_store")
    tmp_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OBJECT_STORE_ENABLE_CAPTURE", "true")
    monkeypatch.setenv("OBJECT_STORE_CAPTURE_POLICY", "scored_only")
    monkeypatch.setenv("OBJECT_STORE_ROOT", str(tmp_root))
    get_settings.cache_clear()

    from app.services import object_store as object_store_service

    monkeypatch.setattr(object_store_service.httpx, "Client", _FakeClient)

    init_db()
    with SessionLocal() as session:
        listing = _seed_listing(session)
        created = capture_listing_assets(
            session,
            listing,
            deal_bucket="potential",
            source_payload={"raw_html": "<html>captured page</html>"},
        )
        session.commit()

        assert created == 2

        second_created = capture_listing_assets(
            session,
            listing,
            deal_bucket="potential",
            source_payload={"raw_html": "<html>captured page</html>"},
        )
        session.commit()
        assert second_created == 0

        rows = session.query(ListingAsset).filter(ListingAsset.listing_id == listing.listing_id).all()
        assert len(rows) == 2
        assert {row.asset_type for row in rows} == {"image", "page_capture"}

        for row in rows:
            path = Path(row.local_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[3] / row.local_path
            assert path.exists()
