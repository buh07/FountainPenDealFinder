import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import RawListingPayload, SearchQuery
from ..core.config import get_settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


class FixtureListingSourceAdapter:
    """Simple adapter backed by local fixture JSON for early development."""

    def __init__(self) -> None:
        settings = get_settings()
        self.fixture_path = _repo_root() / settings.fixture_listings_path

    def _load(self) -> list[RawListingPayload]:
        if not self.fixture_path.exists():
            return []
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _annotate_stale_fallback(item: RawListingPayload) -> RawListingPayload:
        raw_attributes = item.get("raw_attributes")
        if not isinstance(raw_attributes, dict):
            raw_attributes = {}
        updated = dict(raw_attributes)
        updated["fixture_stale_fallback"] = True

        clone = dict(item)
        clone["raw_attributes"] = updated
        return clone

    def by_source(self, source: str) -> list[RawListingPayload]:
        return [item for item in self._load() if str(item.get("source") or "") == source]

    def search(self, query: SearchQuery) -> list[RawListingPayload]:
        keyword = query.keyword.lower().strip()
        matches = []
        for item in self._load():
            title = str(item.get("title") or "").lower()
            if keyword and keyword not in title:
                continue
            price = int(item.get("current_price_jpy") or 0)
            if query.min_price_jpy is not None and price < query.min_price_jpy:
                continue
            if query.max_price_jpy is not None and price > query.max_price_jpy:
                continue
            matches.append(item)
        return matches

    def fetch_listing_detail(self, source_id: str) -> RawListingPayload | None:
        for item in self._load():
            if str(item.get("source_listing_id")) == source_id:
                return item
        return None

    def fetch_listing_images(self, source_id: str) -> list[str]:
        detail = self.fetch_listing_detail(source_id)
        if not detail:
            return []
        images = detail.get("images") or []
        return [str(image) for image in images]

    def get_fresh_window_listings(
        self,
        window_start: datetime,
        category: str,
        source_filter: str | None = None,
    ) -> list[RawListingPayload]:
        rows: list[RawListingPayload] = []
        payload = self.by_source(source_filter) if source_filter else self._load()
        for item in payload:
            listed_at = _parse_datetime(item.get("listed_at"))
            if listed_at is None:
                continue
            if listed_at >= window_start:
                rows.append(item)

        if rows:
            return rows

        if source_filter and payload:
            sorted_payload = sorted(
                payload,
                key=lambda item: (_parse_datetime(item.get("listed_at")) or datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True,
            )
            return [self._annotate_stale_fallback(item) for item in sorted_payload]

        return rows

    def get_ending_auctions(
        self,
        window_start: datetime,
        window_end: datetime,
        category: str,
        source_filter: str | None = None,
    ) -> list[RawListingPayload]:
        rows: list[RawListingPayload] = []
        payload = self.by_source(source_filter) if source_filter else self._load()
        for item in payload:
            if item.get("listing_format") != "auction":
                continue
            ends_at = _parse_datetime(item.get("ends_at"))
            if ends_at is None:
                continue
            if window_start <= ends_at < window_end:
                rows.append(item)

        if rows:
            return rows

        if source_filter:
            auction_payload = [item for item in payload if item.get("listing_format") == "auction"]
            sorted_payload = sorted(
                auction_payload,
                key=lambda item: (_parse_datetime(item.get("ends_at")) or datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True,
            )
            return [self._annotate_stale_fallback(item) for item in sorted_payload]

        return rows
