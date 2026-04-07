from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass
class SearchQuery:
    keyword: str
    category: str = "fountain_pen"
    min_price_jpy: int | None = None
    max_price_jpy: int | None = None


RawListingPayload = dict[str, Any]


class ListingSourceAdapter(Protocol):
    def search(self, query: SearchQuery) -> list[RawListingPayload]:
        ...

    def fetch_listing_detail(self, source_id: str) -> RawListingPayload | None:
        ...

    def fetch_listing_images(self, source_id: str) -> list[str]:
        ...

    def get_fresh_window_listings(
        self,
        window_start: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        ...

    def get_ending_auctions(
        self,
        window_start: datetime,
        window_end: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        ...
