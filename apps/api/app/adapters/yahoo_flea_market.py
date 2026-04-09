import json
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
    normalize_whitespace,
    parse_price_with_status,
    to_utc_iso,
)
from ..core.config import get_settings


class YahooFleaMarketAdapter:
    """Best-effort connector for Yahoo! Fleamarket (PayPay Flea)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.yahoo_flea_market_base_url.rstrip("/")
        self.search_path = settings.yahoo_flea_market_search_path
        self.default_keyword = settings.yahoo_flea_market_keyword
        self.max_results = max(1, settings.yahoo_flea_market_max_results)
        self.timeout = settings.yahoo_flea_market_timeout_seconds
        self.verify_ssl = settings.yahoo_flea_market_verify_ssl
        self.request_interval_seconds = max(0.0, settings.yahoo_flea_market_request_interval_seconds)

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

    @staticmethod
    def _mark_price_parse_error(raw_attributes: dict[str, Any], parse_error: bool) -> dict[str, Any]:
        updated = dict(raw_attributes)
        if parse_error:
            updated["price_parse_error"] = True
        else:
            updated.pop("price_parse_error", None)
        return updated

    def _parse_ldjson_itemlist(self, soup: BeautifulSoup) -> list[RawListingPayload]:
        now_iso = to_utc_iso(datetime.now(timezone.utc))
        rows: list[RawListingPayload] = []

        for script in soup.select('script[type="application/ld+json"]'):
            raw = (script.string or script.get_text() or "").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue

            blocks = payload if isinstance(payload, list) else [payload]
            for block in blocks:
                if not isinstance(block, dict) or block.get("@type") != "ItemList":
                    continue
                for element in block.get("itemListElement", []):
                    if not isinstance(element, dict):
                        continue
                    item = element.get("item", element)
                    if not isinstance(item, dict):
                        continue

                    url = str(item.get("url") or "")
                    source_listing_id = extract_id_from_url(url, r"/item/([a-zA-Z0-9_-]+)")
                    if not source_listing_id:
                        continue

                    title = normalize_whitespace(str(item.get("name") or ""))
                    if not title:
                        continue

                    price: int | None = None
                    price_parse_error = False
                    offers = item.get("offers")
                    if isinstance(offers, dict):
                        if "price" in offers and offers.get("price") not in (None, ""):
                            try:
                                price = int(float(str(offers.get("price"))))
                            except ValueError:
                                price_parse_error = True
                    elif offers not in (None, ""):
                        price_parse_error = True

                    if price is None:
                        alt_price, alt_parse_error = parse_price_with_status(
                            f"{title} {item.get('description') or ''}"
                        )
                        if alt_price is not None:
                            price = alt_price
                        price_parse_error = price_parse_error or alt_parse_error

                    rows.append(
                        {
                            "source": "yahoo_flea_market",
                            "source_listing_id": source_listing_id,
                            "url": url,
                            "title": title,
                            "description_raw": str(item.get("description") or ""),
                            "images": [],
                            "seller_id": None,
                            "seller_rating": None,
                            "listing_format": "buy_now",
                            "current_price_jpy": int(price or 0),
                            "price_buy_now_jpy": int(price or 0),
                            "domestic_shipping_jpy": 0,
                            "bid_count": None,
                            "listed_at": now_iso,
                            "ends_at": None,
                            "location_prefecture": None,
                            "condition_text": None,
                            "lot_size_hint": 1,
                            "raw_attributes": self._mark_price_parse_error(
                                {"connector": "yahoo_flea_market_ldjson"},
                                parse_error=price_parse_error and (not price or price <= 0),
                            ),
                        }
                    )

        return rows

    def _parse_anchor_items(self, soup: BeautifulSoup) -> list[RawListingPayload]:
        now_iso = to_utc_iso(datetime.now(timezone.utc))
        rows: list[RawListingPayload] = []
        seen_ids: set[str] = set()

        for anchor in soup.select('a[href*="/item/"]'):
            href = str(anchor.get("href") or "")
            if not href:
                continue

            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            source_listing_id = extract_id_from_url(full_url, r"/item/([a-zA-Z0-9_-]+)")
            if not source_listing_id or source_listing_id in seen_ids:
                continue

            title = normalize_whitespace(anchor.get_text(" ", strip=True))
            if not title:
                continue

            container = anchor.parent.get_text(" ", strip=True) if anchor.parent else title
            price, parse_error = parse_price_with_status(container)
            if price is None:
                alt_price, alt_parse_error = parse_price_with_status(title)
                if alt_price is not None:
                    price = alt_price
                parse_error = parse_error or alt_parse_error
            seen_ids.add(source_listing_id)

            rows.append(
                {
                    "source": "yahoo_flea_market",
                    "source_listing_id": source_listing_id,
                    "url": full_url,
                    "title": title,
                    "description_raw": "",
                    "images": [],
                    "seller_id": None,
                    "seller_rating": None,
                    "listing_format": "buy_now",
                    "current_price_jpy": int(price or 0),
                    "price_buy_now_jpy": int(price or 0),
                    "domestic_shipping_jpy": 0,
                    "bid_count": None,
                    "listed_at": now_iso,
                    "ends_at": None,
                    "location_prefecture": None,
                    "condition_text": None,
                    "lot_size_hint": 1,
                    "raw_attributes": self._mark_price_parse_error(
                        {"connector": "yahoo_flea_market_html"},
                        parse_error=parse_error and (not price or price <= 0),
                    ),
                }
            )

            if len(rows) >= self.max_results:
                break

        return rows

    def _repair_price_with_detail(self, row: RawListingPayload) -> RawListingPayload:
        raw_attributes = row.get("raw_attributes")
        if not isinstance(raw_attributes, dict):
            raw_attributes = {}
        if not raw_attributes.get("price_parse_error"):
            return row

        source_listing_id = str(row.get("source_listing_id") or "")
        if not source_listing_id:
            return row

        try:
            detail = self.fetch_listing_detail(source_listing_id)
        except Exception:
            return row
        if not detail:
            return row

        detail_price = int(detail.get("price_buy_now_jpy") or detail.get("current_price_jpy") or 0)
        if detail_price <= 0:
            return row

        repaired = dict(row)
        repaired["current_price_jpy"] = detail_price
        repaired["price_buy_now_jpy"] = detail_price
        repaired_attributes = dict(raw_attributes)
        repaired_attributes["price_repaired_from_detail"] = True
        repaired_attributes.pop("price_parse_error", None)
        repaired["raw_attributes"] = repaired_attributes
        return repaired

    def _search_internal(self, keyword: str) -> list[RawListingPayload]:
        search_paths = [self.search_path, "/search", "/search/"]
        query_keys = ["query", "keyword", "q"]
        seen_paths: set[str] = set()

        for search_path in search_paths:
            normalized_path = search_path.strip() or "/search"
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)

            for query_key in query_keys:
                try:
                    html = self._request(normalized_path, params={query_key: keyword})
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in {404, 410}:
                        continue
                    continue
                except httpx.HTTPError:
                    continue

                soup = BeautifulSoup(html, "html.parser")
                parsed_rows = self._parse_ldjson_itemlist(soup)
                if not parsed_rows:
                    parsed_rows = self._parse_anchor_items(soup)

                deduped: dict[str, RawListingPayload] = {}
                for row in parsed_rows:
                    source_listing_id = str(row.get("source_listing_id") or "")
                    if source_listing_id:
                        deduped[source_listing_id] = row

                if not deduped:
                    continue

                rows = list(deduped.values())[: self.max_results]
                return [self._repair_price_with_detail(row) for row in rows]

        return []

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
            if src and ("yimg" in src or "flea" in src):
                image_urls.append(src)

        body_text = soup.get_text(" ", strip=True)
        price, parse_error = parse_price_with_status(body_text)
        if price is None:
            alt_price, alt_parse_error = parse_price_with_status(title)
            if alt_price is not None:
                price = alt_price
            parse_error = parse_error or alt_parse_error

        return {
            "source": "yahoo_flea_market",
            "source_listing_id": source_id,
            "url": urljoin(self.base_url + "/", path.lstrip("/")),
            "title": title,
            "description_raw": "",
            "images": dedupe_preserve_order(image_urls),
            "seller_id": None,
            "seller_rating": None,
            "listing_format": "buy_now",
            "current_price_jpy": int(price or 0),
            "price_buy_now_jpy": int(price or 0),
            "domestic_shipping_jpy": 0,
            "bid_count": None,
            "listed_at": to_utc_iso(datetime.now(timezone.utc)),
            "ends_at": None,
            "location_prefecture": None,
            "condition_text": None,
            "lot_size_hint": 1,
            "raw_attributes": self._mark_price_parse_error(
                {"connector": "yahoo_flea_market_detail"},
                parse_error=parse_error and (not price or price <= 0),
            ),
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
