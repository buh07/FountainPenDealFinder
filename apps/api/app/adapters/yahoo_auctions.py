import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .base import RawListingPayload, SearchQuery
from .html_helpers import parse_price_with_status
from ..core.config import get_settings


def _to_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _extract_price_jpy(text: str) -> int | None:
    price, _ = parse_price_with_status(text)
    return price


def _extract_auction_id(url: str) -> str | None:
    match = re.search(r"/jp/auction/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    return None


def _parse_datetime_maybe(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip().replace("Z", "+00:00")
    jst = ZoneInfo("Asia/Tokyo")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=jst)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    jp_match = re.search(
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日\s*(\d{1,2})[:時]\s*(\d{1,2})?",
        normalized,
    )
    if jp_match:
        year = int(jp_match.group(1))
        month = int(jp_match.group(2))
        day = int(jp_match.group(3))
        hour = int(jp_match.group(4))
        minute = int(jp_match.group(5) or 0)
        return datetime(year, month, day, hour, minute, tzinfo=jst).astimezone(timezone.utc)

    return None


class YahooAuctionsAdapter:
    """Connector for Yahoo! JAPAN Auctions search and listing pages."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.yahoo_auctions_base_url.rstrip("/")
        self.search_path = settings.yahoo_auctions_search_path
        self.default_keyword = settings.yahoo_auctions_keyword
        self.max_results = max(1, settings.yahoo_auctions_max_results)
        self.timeout = settings.yahoo_auctions_timeout_seconds
        self.verify_ssl = settings.yahoo_auctions_verify_ssl
        self.request_interval_seconds = max(0.0, settings.yahoo_auctions_request_interval_seconds)

    def _request(self, path: str, params: dict[str, Any] | None = None) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        with httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            verify=self.verify_ssl,
            headers=headers,
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
        if repaired.get("listing_format") == "buy_now":
            repaired["price_buy_now_jpy"] = detail_price

        repaired_attributes = dict(raw_attributes)
        repaired_attributes["price_repaired_from_detail"] = True
        repaired_attributes.pop("price_parse_error", None)
        repaired["raw_attributes"] = repaired_attributes
        return repaired

    def _parse_ldjson_itemlist(self, soup: BeautifulSoup) -> list[RawListingPayload]:
        now_iso = _to_utc_iso(datetime.now(timezone.utc))
        parsed: list[RawListingPayload] = []

        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text() or ""
            raw = raw.strip()
            if not raw:
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("@type") != "ItemList":
                    continue

                for element in block.get("itemListElement", []):
                    if not isinstance(element, dict):
                        continue
                    item = element.get("item", element)
                    if not isinstance(item, dict):
                        continue

                    url = str(item.get("url") or "")
                    source_listing_id = _extract_auction_id(url)
                    if not source_listing_id:
                        continue

                    offers = item.get("offers") or {}
                    price: int | None = None
                    price_parse_error = False
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
                            f"{item.get('name') or ''} {item.get('description') or ''}"
                        )
                        if alt_price is not None:
                            price = alt_price
                        price_parse_error = price_parse_error or alt_parse_error

                    ends_at_dt = _parse_datetime_maybe(
                        item.get("endDate") if isinstance(item.get("endDate"), str) else None
                    )
                    listed_at_dt = _parse_datetime_maybe(
                        item.get("startDate") if isinstance(item.get("startDate"), str) else None
                    )

                    image_value = item.get("image")
                    if isinstance(image_value, str):
                        images = [image_value]
                    elif isinstance(image_value, list):
                        images = [str(value) for value in image_value if isinstance(value, str)]
                    else:
                        images = []

                    parsed.append(
                        {
                            "source": "yahoo_auctions",
                            "source_listing_id": source_listing_id,
                            "url": url,
                            "title": str(item.get("name") or ""),
                            "description_raw": str(item.get("description") or ""),
                            "images": images,
                            "seller_id": None,
                            "seller_rating": None,
                            "listing_format": "auction",
                            "current_price_jpy": int(price or 0),
                            "price_buy_now_jpy": None,
                            "domestic_shipping_jpy": 0,
                            "bid_count": None,
                            "listed_at": _to_utc_iso(listed_at_dt) or now_iso,
                            "ends_at": _to_utc_iso(ends_at_dt),
                            "location_prefecture": None,
                            "condition_text": None,
                            "lot_size_hint": 1,
                            "raw_attributes": self._mark_price_parse_error(
                                {"connector": "yahoo_auctions_ldjson"},
                                parse_error=price_parse_error and (not price or price <= 0),
                            ),
                        }
                    )

        return parsed

    def _parse_anchor_fallback(self, soup: BeautifulSoup) -> list[RawListingPayload]:
        now_iso = _to_utc_iso(datetime.now(timezone.utc))
        parsed: list[RawListingPayload] = []
        seen: set[str] = set()

        for anchor in soup.select('a[href*="/jp/auction/"]'):
            href = anchor.get("href")
            if not href:
                continue

            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            source_listing_id = _extract_auction_id(full_url)
            if not source_listing_id or source_listing_id in seen:
                continue
            seen.add(source_listing_id)

            title = " ".join(anchor.get_text(" ", strip=True).split())
            if not title:
                continue

            container_text = ""
            if anchor.parent is not None:
                container_text = " ".join(anchor.parent.get_text(" ", strip=True).split())

            price, parse_error = parse_price_with_status(container_text)
            if price is None:
                alt_price, alt_parse_error = parse_price_with_status(title)
                if alt_price is not None:
                    price = alt_price
                parse_error = parse_error or alt_parse_error
            ends_at = _parse_datetime_maybe(container_text)

            parsed.append(
                {
                    "source": "yahoo_auctions",
                    "source_listing_id": source_listing_id,
                    "url": full_url,
                    "title": title,
                    "description_raw": "",
                    "images": [],
                    "seller_id": None,
                    "seller_rating": None,
                    "listing_format": "auction",
                    "current_price_jpy": int(price or 0),
                    "price_buy_now_jpy": None,
                    "domestic_shipping_jpy": 0,
                    "bid_count": None,
                    "listed_at": now_iso,
                    "ends_at": _to_utc_iso(ends_at),
                    "location_prefecture": None,
                    "condition_text": None,
                    "lot_size_hint": 1,
                    "raw_attributes": self._mark_price_parse_error(
                        {"connector": "yahoo_auctions_anchor"},
                        parse_error=parse_error and (not price or price <= 0),
                    ),
                }
            )

        return parsed

    def _search_internal(self, keyword: str) -> list[RawListingPayload]:
        html = self._request(
            self.search_path,
            params={"p": keyword},
        )
        soup = BeautifulSoup(html, "html.parser")

        parsed = self._parse_ldjson_itemlist(soup)
        if not parsed:
            parsed = self._parse_anchor_fallback(soup)

        deduped: dict[str, RawListingPayload] = {}
        for row in parsed:
            source_listing_id = str(row.get("source_listing_id") or "")
            if source_listing_id:
                deduped[source_listing_id] = row

        rows = list(deduped.values())[: self.max_results]
        return [self._repair_price_with_detail(row) for row in rows]

    def search(self, query: SearchQuery) -> list[RawListingPayload]:
        keyword = query.keyword.strip() or self.default_keyword
        return self._search_internal(keyword)

    def fetch_listing_detail(self, source_id: str) -> RawListingPayload | None:
        path = f"https://page.auctions.yahoo.co.jp/jp/auction/{source_id}"
        html = self._request(path)
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.select_one("h1")
        title = " ".join(title_tag.get_text(" ", strip=True).split()) if title_tag else ""
        if not title:
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            title = page_title

        description_meta = soup.select_one('meta[name="description"]')
        description_raw = description_meta.get("content", "") if description_meta else ""

        image_urls: list[str] = []
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image and og_image.get("content"):
            image_urls.append(str(og_image.get("content")))

        for img in soup.select("img[src]"):
            src = str(img.get("src") or "")
            if not src:
                continue
            if "yimg" in src or "auction" in src:
                image_urls.append(src)

        dedup_images: list[str] = []
        seen = set()
        for image in image_urls:
            if image not in seen:
                dedup_images.append(image)
                seen.add(image)

        body_text = soup.get_text(" ", strip=True)
        price, parse_error = parse_price_with_status(body_text)
        if price is None:
            alt_price, alt_parse_error = parse_price_with_status(f"{title} {description_raw}")
            if alt_price is not None:
                price = alt_price
            parse_error = parse_error or alt_parse_error
        ends_at = _parse_datetime_maybe(body_text)

        return {
            "source": "yahoo_auctions",
            "source_listing_id": source_id,
            "url": f"https://page.auctions.yahoo.co.jp/jp/auction/{source_id}",
            "title": title,
            "description_raw": description_raw,
            "images": dedup_images,
            "seller_id": None,
            "seller_rating": None,
            "listing_format": "auction",
            "current_price_jpy": int(price or 0),
            "price_buy_now_jpy": None,
            "domestic_shipping_jpy": 0,
            "bid_count": None,
            "listed_at": _to_utc_iso(datetime.now(timezone.utc)),
            "ends_at": _to_utc_iso(ends_at),
            "location_prefecture": None,
            "condition_text": None,
            "lot_size_hint": 1,
            "raw_attributes": self._mark_price_parse_error(
                {"connector": "yahoo_auctions_detail"},
                parse_error=parse_error and (not price or price <= 0),
            ),
        }

    def fetch_listing_images(self, source_id: str) -> list[str]:
        detail = self.fetch_listing_detail(source_id)
        if detail is None:
            return []
        images = detail.get("images") or []
        return [str(image) for image in images]

    def get_fresh_window_listings(
        self,
        window_start: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        rows = self._search_internal(self.default_keyword)
        fresh: list[RawListingPayload] = []
        for row in rows:
            listed_at = _parse_datetime_maybe(str(row.get("listed_at") or ""))
            if listed_at is None or listed_at >= window_start:
                fresh.append(row)
        return fresh

    def get_ending_auctions(
        self,
        window_start: datetime,
        window_end: datetime,
        category: str,
    ) -> list[RawListingPayload]:
        rows = self._search_internal(self.default_keyword)
        ending: list[RawListingPayload] = []
        for row in rows:
            ends_at = _parse_datetime_maybe(str(row.get("ends_at") or ""))
            if ends_at is None:
                continue
            if window_start <= ends_at < window_end:
                ending.append(row)
        return ending
