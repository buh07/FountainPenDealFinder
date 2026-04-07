import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import RawListingPayload, SearchQuery
from .html_helpers import (
    default_headers,
    dedupe_preserve_order,
    extract_id_from_url,
    extract_price_jpy,
    normalize_whitespace,
    to_utc_iso,
)
from ..core.config import get_settings


class MercariAdapter:
    """Best-effort connector for Mercari search pages."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.mercari_base_url.rstrip("/")
        self.search_path = settings.mercari_search_path
        self.default_keyword = settings.mercari_keyword
        self.max_results = max(1, settings.mercari_max_results)
        self.timeout = settings.mercari_timeout_seconds
        self.verify_ssl = settings.mercari_verify_ssl
        self.request_interval_seconds = max(0.0, settings.mercari_request_interval_seconds)

    def _request(self, path: str, params: dict[str, Any] | None = None) -> str:
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            verify=self.verify_ssl,
            headers=default_headers(),
        ) as client:
            response = client.get(urljoin(self.base_url + "/", path.lstrip("/")), params=params)
            response.raise_for_status()
            if self.request_interval_seconds > 0:
                time.sleep(self.request_interval_seconds)
            return response.text

    def _search_internal(self, keyword: str) -> list[RawListingPayload]:
        html = self._request(self.search_path, params={"keyword": keyword})
        soup = BeautifulSoup(html, "html.parser")

        now_iso = to_utc_iso(datetime.now(timezone.utc))
        rows: list[RawListingPayload] = []
        seen_ids: set[str] = set()

        for anchor in soup.select('a[href*="/item/"]'):
            href = str(anchor.get("href") or "")
            if not href:
                continue

            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            source_listing_id = extract_id_from_url(full_url, r"/item/([a-zA-Z0-9]+)")
            if not source_listing_id or source_listing_id in seen_ids:
                continue

            title = normalize_whitespace(anchor.get_text(" ", strip=True))
            if not title:
                continue

            container = anchor.parent.get_text(" ", strip=True) if anchor.parent else title
            price = extract_price_jpy(container) or 0
            seen_ids.add(source_listing_id)

            rows.append(
                {
                    "source": "mercari",
                    "source_listing_id": source_listing_id,
                    "url": full_url,
                    "title": title,
                    "description_raw": "",
                    "images": [],
                    "seller_id": None,
                    "seller_rating": None,
                    "listing_format": "buy_now",
                    "current_price_jpy": price,
                    "price_buy_now_jpy": price,
                    "domestic_shipping_jpy": 0,
                    "bid_count": None,
                    "listed_at": now_iso,
                    "ends_at": None,
                    "location_prefecture": None,
                    "condition_text": None,
                    "lot_size_hint": 1,
                    "raw_attributes": {"connector": "mercari_html"},
                }
            )

            if len(rows) >= self.max_results:
                break

        return rows

    def search(self, query: SearchQuery) -> list[RawListingPayload]:
        keyword = query.keyword.strip() or self.default_keyword
        return self._search_internal(keyword)

    def fetch_listing_detail(self, source_id: str) -> RawListingPayload | None:
        path = f"/item/{source_id}"
        html = self._request(path)
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.select_one("h1")
        title = normalize_whitespace(title_tag.get_text(" ", strip=True)) if title_tag else ""
        if not title:
            title = normalize_whitespace(soup.title.get_text(" ", strip=True)) if soup.title else ""

        image_urls: list[str] = []
        for image in soup.select("img[src]"):
            src = str(image.get("src") or "")
            if src and ("merpay" in src or "mercari" in src):
                image_urls.append(src)

        return {
            "source": "mercari",
            "source_listing_id": source_id,
            "url": urljoin(self.base_url + "/", path.lstrip("/")),
            "title": title,
            "description_raw": "",
            "images": dedupe_preserve_order(image_urls),
            "seller_id": None,
            "seller_rating": None,
            "listing_format": "buy_now",
            "current_price_jpy": 0,
            "price_buy_now_jpy": 0,
            "domestic_shipping_jpy": 0,
            "bid_count": None,
            "listed_at": to_utc_iso(datetime.now(timezone.utc)),
            "ends_at": None,
            "location_prefecture": None,
            "condition_text": None,
            "lot_size_hint": 1,
            "raw_attributes": {"connector": "mercari_detail"},
        }

    def fetch_listing_images(self, source_id: str) -> list[str]:
        detail = self.fetch_listing_detail(source_id)
        if not detail:
            return []
        return [str(value) for value in detail.get("images") or []]

    def get_fresh_window_listings(
        self,
        window_start: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        return self._search_internal(self.default_keyword)

    def get_ending_auctions(
        self,
        window_start: datetime,
        window_end: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        return []
