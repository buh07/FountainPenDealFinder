from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PriceStatus = Literal["valid", "missing", "parse_error"]


def _to_positive_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def has_positive_price(current_price_jpy: Any, price_buy_now_jpy: Any) -> bool:
    return _to_positive_int(current_price_jpy) > 0 or _to_positive_int(price_buy_now_jpy) > 0


def parse_raw_attributes(raw_attributes: Any) -> dict[str, Any]:
    if isinstance(raw_attributes, dict):
        return dict(raw_attributes)
    if isinstance(raw_attributes, str) and raw_attributes:
        try:
            payload = json.loads(raw_attributes)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return {}
    return {}


def derive_price_status(
    current_price_jpy: Any,
    price_buy_now_jpy: Any,
    raw_attributes: Any,
) -> PriceStatus:
    if has_positive_price(current_price_jpy, price_buy_now_jpy):
        return "valid"

    attrs = parse_raw_attributes(raw_attributes)
    if bool(attrs.get("price_parse_error")):
        return "parse_error"
    return "missing"


def get_default_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def local_day_bounds_utc(target_date: date, default_tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(target_date, time.min, tzinfo=default_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
