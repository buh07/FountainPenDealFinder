import logging
import math
from collections.abc import Iterable
from datetime import datetime, timezone
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import CouponRule, ProxyOptionEstimate, ProxyPricingPolicy, RawListing


logger = logging.getLogger(__name__)

PROXY_MARKETPLACE_COMPATIBILITY: dict[str, set[str]] = {
    "Buyee": {"yahoo_auctions", "yahoo_flea_market", "mercari", "rakuma"},
    "FromJapan": {"yahoo_auctions", "yahoo_flea_market", "mercari", "rakuma"},
    "Neokyo": {"yahoo_auctions", "mercari", "rakuma"},
    "None": {"yahoo_auctions", "yahoo_flea_market", "mercari", "rakuma"},
}


DEFAULT_PROXY_POLICIES = [
    {
        "proxy_name": "Buyee",
        "marketplace_source": None,
        "service_fee_jpy": 300,
        "intl_shipping_jpy": 1800,
        "min_buy_price_jpy": 0,
        "max_buy_price_jpy": None,
        "priority": 10,
    },
    {
        "proxy_name": "FromJapan",
        "marketplace_source": None,
        "service_fee_jpy": 250,
        "intl_shipping_jpy": 1800,
        "min_buy_price_jpy": 0,
        "max_buy_price_jpy": None,
        "priority": 10,
    },
    {
        "proxy_name": "Neokyo",
        "marketplace_source": None,
        "service_fee_jpy": 275,
        "intl_shipping_jpy": 1850,
        "min_buy_price_jpy": 0,
        "max_buy_price_jpy": None,
        "priority": 10,
    },
]


DEFAULT_COUPON_RULES = [
    {
        "proxy_name": "Buyee",
        "marketplace_source": "rakuma",
        "coupon_id": "buyee_servicefee50",
        "discount_type": "service_fee_pct",
        "discount_value": 0.5,
        "min_buy_price_jpy": 0,
        "max_discount_jpy": None,
        "is_stackable": False,
        "priority": 20,
    },
    {
        "proxy_name": "Buyee",
        "marketplace_source": "yahoo_flea_market",
        "coupon_id": "buyee_servicefee50",
        "discount_type": "service_fee_pct",
        "discount_value": 0.5,
        "min_buy_price_jpy": 0,
        "max_discount_jpy": None,
        "is_stackable": False,
        "priority": 20,
    },
    {
        "proxy_name": "FromJapan",
        "marketplace_source": "mercari",
        "coupon_id": "fromjapan_servicefee50",
        "discount_type": "service_fee_pct",
        "discount_value": 0.5,
        "min_buy_price_jpy": 0,
        "max_discount_jpy": None,
        "is_stackable": False,
        "priority": 20,
    },
    {
        "proxy_name": "FromJapan",
        "marketplace_source": "yahoo_auctions",
        "coupon_id": "fromjapan_servicefee50",
        "discount_type": "service_fee_pct",
        "discount_value": 0.5,
        "min_buy_price_jpy": 0,
        "max_discount_jpy": None,
        "is_stackable": False,
        "priority": 20,
    },
    {
        "proxy_name": "FromJapan",
        "marketplace_source": None,
        "coupon_id": "fromjapan_item500",
        "discount_type": "flat_jpy",
        "discount_value": 500,
        "min_buy_price_jpy": 60000,
        "max_discount_jpy": 500,
        "is_stackable": True,
        "priority": 30,
    },
]


def _seed_proxy_rules_if_needed(session: Session) -> None:
    has_policy = session.scalar(select(ProxyPricingPolicy.policy_id).limit(1)) is not None
    if not has_policy:
        for payload in DEFAULT_PROXY_POLICIES:
            session.add(
                ProxyPricingPolicy(
                    proxy_name=payload["proxy_name"],
                    marketplace_source=payload["marketplace_source"],
                    service_fee_jpy=payload["service_fee_jpy"],
                    intl_shipping_jpy=payload["intl_shipping_jpy"],
                    min_buy_price_jpy=payload["min_buy_price_jpy"],
                    max_buy_price_jpy=payload["max_buy_price_jpy"],
                    priority=payload["priority"],
                    is_active=True,
                )
            )

    has_coupon = session.scalar(select(CouponRule.coupon_rule_id).limit(1)) is not None
    if not has_coupon:
        for payload in DEFAULT_COUPON_RULES:
            session.add(
                CouponRule(
                    proxy_name=payload["proxy_name"],
                    marketplace_source=payload["marketplace_source"],
                    coupon_id=payload["coupon_id"],
                    discount_type=payload["discount_type"],
                    discount_value=payload["discount_value"],
                    min_buy_price_jpy=payload["min_buy_price_jpy"],
                    max_discount_jpy=payload["max_discount_jpy"],
                    is_stackable=payload["is_stackable"],
                    priority=payload["priority"],
                    is_active=True,
                )
            )

    session.flush()


def _is_active_now(starts_at: datetime | None, ends_at: datetime | None, now: datetime) -> bool:
    if starts_at and starts_at > now:
        return False
    if ends_at and ends_at < now:
        return False
    return True


def _is_proxy_compatible(proxy_name: str, marketplace_source: str) -> bool:
    allowed_sources = PROXY_MARKETPLACE_COMPATIBILITY.get(proxy_name)
    if not allowed_sources:
        return True
    return marketplace_source in allowed_sources


def _listing_raw_attributes(listing: RawListing) -> dict:
    raw = str(listing.raw_attributes_json or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_time_proxy_names(listing: RawListing) -> set[str]:
    raw_attributes = _listing_raw_attributes(listing)
    marker = raw_attributes.get("proxy_first_time_user")

    if marker is True:
        return {"*"}
    if marker is False or marker is None:
        return set()
    if isinstance(marker, str):
        candidates = [part.strip() for part in marker.split(",") if part.strip()]
        return {value.lower() for value in candidates}
    if isinstance(marker, Iterable):
        names: set[str] = set()
        for value in marker:
            normalized = str(value or "").strip().lower()
            if normalized:
                names.add(normalized)
        return names
    return set()


def _first_time_penalty_for_proxy(listing: RawListing, proxy_name: str) -> int:
    settings = get_settings()
    marker = _first_time_proxy_names(listing)
    if "*" in marker or proxy_name.lower() in marker:
        return max(0, int(settings.proxy_first_time_user_penalty_jpy))
    return 0


def _apply_coupon_discount(
    rule: CouponRule,
    buy_price_jpy: int,
    service_fee_jpy: int,
) -> int:
    if rule.discount_type == "service_fee_pct":
        discount = int(service_fee_jpy * float(rule.discount_value))
    elif rule.discount_type == "item_price_pct":
        discount = int(buy_price_jpy * float(rule.discount_value))
    else:
        discount = int(rule.discount_value)

    if rule.max_discount_jpy is not None:
        discount = min(discount, int(rule.max_discount_jpy))

    return max(0, discount)


def _pick_coupon_set(
    rules: list[CouponRule],
    buy_price_jpy: int,
    service_fee_jpy: int,
) -> tuple[int, str | None]:
    # Exact optimizer under current rule semantics:
    # - choose any subset of stackable coupons
    # - choose at most one non-stackable coupon
    # Tie-break for deterministic output:
    # 1) higher total discount, 2) fewer coupons, 3) lexical coupon id order.
    normalized: dict[tuple[str, bool], int] = {}
    for rule in rules:
        if buy_price_jpy < int(rule.min_buy_price_jpy or 0):
            continue
        discount = _apply_coupon_discount(rule, buy_price_jpy, service_fee_jpy)
        if discount <= 0:
            continue
        key = (rule.coupon_id, bool(rule.is_stackable))
        existing = normalized.get(key, 0)
        if discount > existing:
            normalized[key] = discount

    stackable = sorted(
        [(coupon_id, discount) for (coupon_id, is_stackable), discount in normalized.items() if is_stackable],
        key=lambda item: item[0],
    )
    non_stackable = sorted(
        [(coupon_id, discount) for (coupon_id, is_stackable), discount in normalized.items() if not is_stackable],
        key=lambda item: item[0],
    )

    best_discount = 0
    best_coupon_ids: list[str] = []

    settings = get_settings()
    max_exact_stackable = max(1, int(settings.proxy_coupon_max_exact_stackable))
    fallback_top_stackable = max(1, int(settings.proxy_coupon_fallback_top_stackable))

    if len(stackable) > max_exact_stackable:
        ranked = sorted(stackable, key=lambda item: (-int(item[1]), item[0]))
        keep_count = min(len(ranked), max_exact_stackable, fallback_top_stackable)
        stackable = sorted(ranked[:keep_count], key=lambda item: item[0])
        logger.warning(
            "Coupon optimizer stackable set exceeded exact cap; using reduced candidate set",
            extra={
                "stackable_total": len(ranked),
                "stackable_kept": keep_count,
            },
        )

    stack_count = len(stackable)
    for mask in range(1 << stack_count):
        chosen_stackable_ids: list[str] = []
        stackable_discount = 0
        for idx in range(stack_count):
            if mask & (1 << idx):
                coupon_id, discount = stackable[idx]
                chosen_stackable_ids.append(coupon_id)
                stackable_discount += int(discount)

        non_stackable_choices: list[tuple[str | None, int]] = [(None, 0)]
        non_stackable_choices.extend([(coupon_id, int(discount)) for coupon_id, discount in non_stackable])

        for non_stackable_id, non_stackable_discount in non_stackable_choices:
            selected_ids = list(chosen_stackable_ids)
            if non_stackable_id:
                if non_stackable_id in selected_ids:
                    continue
                selected_ids.append(non_stackable_id)
            selected_ids = sorted(set(selected_ids))

            total_discount = stackable_discount + non_stackable_discount
            is_better = False
            if total_discount > best_discount:
                is_better = True
            elif total_discount == best_discount:
                if len(selected_ids) < len(best_coupon_ids):
                    is_better = True
                elif len(selected_ids) == len(best_coupon_ids) and tuple(selected_ids) < tuple(best_coupon_ids):
                    is_better = True

            if is_better:
                best_discount = int(total_discount)
                best_coupon_ids = selected_ids

    if not best_coupon_ids:
        return int(best_discount), None
    return int(best_discount), "+".join(best_coupon_ids)


def estimate_proxy_deals(
    session: Session,
    listing: RawListing,
    buy_price_jpy: int,
    resale_reference_jpy: int,
) -> list[dict]:
    _seed_proxy_rules_if_needed(session)

    now = datetime.now(timezone.utc)
    domestic_shipping = listing.domestic_shipping_jpy or 1200

    policy_rows = session.scalars(
        select(ProxyPricingPolicy)
        .where(ProxyPricingPolicy.is_active.is_(True))
        .order_by(ProxyPricingPolicy.priority.asc())
    ).all()
    policies: dict[str, ProxyPricingPolicy] = {}
    for row in policy_rows:
        if not _is_proxy_compatible(row.proxy_name, listing.source):
            continue
        if row.marketplace_source and row.marketplace_source != listing.source:
            continue
        if buy_price_jpy < int(row.min_buy_price_jpy or 0):
            continue
        if row.max_buy_price_jpy is not None and buy_price_jpy > int(row.max_buy_price_jpy):
            continue
        if not _is_active_now(row.starts_at, row.ends_at, now):
            continue
        if row.proxy_name not in policies:
            policies[row.proxy_name] = row

    coupon_rows = session.scalars(
        select(CouponRule)
        .where(CouponRule.is_active.is_(True))
        .order_by(CouponRule.priority.asc())
    ).all()
    coupons_by_proxy: dict[str, list[CouponRule]] = {}
    for rule in coupon_rows:
        if rule.marketplace_source and rule.marketplace_source != listing.source:
            continue
        if not _is_active_now(rule.starts_at, rule.ends_at, now):
            continue
        coupons_by_proxy.setdefault(rule.proxy_name, []).append(rule)

    payloads: list[dict] = []
    for proxy_name, policy in policies.items():
        coupon_discount, coupon_id = _pick_coupon_set(
            coupons_by_proxy.get(proxy_name, []),
            buy_price_jpy=buy_price_jpy,
            service_fee_jpy=int(policy.service_fee_jpy),
        )

        first_time_penalty = _first_time_penalty_for_proxy(listing, proxy_name)
        total_cost = max(
            0,
            buy_price_jpy
            + domestic_shipping
            + int(policy.service_fee_jpy)
            + int(policy.intl_shipping_jpy)
            + first_time_penalty
            - coupon_discount,
        )

        expected_profit = resale_reference_jpy - total_cost
        expected_profit_pct = expected_profit / max(total_cost, 1)
        cost_confidence = 0.74
        if coupon_id:
            coupon_count = len(coupon_id.split("+"))
            cost_confidence = max(0.55, 0.72 - (coupon_count * 0.03))
        if first_time_penalty > 0:
            cost_confidence = max(0.5, cost_confidence - 0.05)

        risk_adjusted_total_cost = int(math.ceil(total_cost / max(cost_confidence, 0.45)))

        payloads.append(
            {
                "proxy_name": proxy_name,
                "total_cost_jpy": int(total_cost),
                "resale_reference_jpy": int(resale_reference_jpy),
                "expected_profit_jpy": int(expected_profit),
                "expected_profit_pct": round(expected_profit_pct, 4),
                "coupon_id": coupon_id,
                "coupon_discount_jpy": int(coupon_discount),
                "cost_confidence": round(cost_confidence, 3),
                "risk_adjusted_total_cost_jpy": int(risk_adjusted_total_cost),
                "first_time_penalty_jpy": int(first_time_penalty),
                "compatible_with_marketplace": True,
                "compatibility_note": None,
                "is_recommended": False,
                "is_recommended_risk_adjusted": False,
                "arbitrage_rank": None,
            }
        )

    if not payloads:
        # Safety fallback if policy data is absent or filtered out.
        payloads.append(
            {
                "proxy_name": "None",
                "total_cost_jpy": int(max(0, buy_price_jpy + domestic_shipping)),
                "resale_reference_jpy": int(resale_reference_jpy),
                "expected_profit_jpy": int(resale_reference_jpy - (buy_price_jpy + domestic_shipping)),
                "expected_profit_pct": round(
                    (resale_reference_jpy - (buy_price_jpy + domestic_shipping))
                    / max(1, buy_price_jpy + domestic_shipping),
                    4,
                ),
                "coupon_id": None,
                "coupon_discount_jpy": 0,
                "cost_confidence": 0.65,
                "risk_adjusted_total_cost_jpy": int(
                    math.ceil(max(0, buy_price_jpy + domestic_shipping) / 0.65)
                ),
                "first_time_penalty_jpy": 0,
                "compatible_with_marketplace": True,
                "compatibility_note": None,
                "is_recommended": False,
                "is_recommended_risk_adjusted": False,
                "arbitrage_rank": None,
            }
        )

    payloads.sort(
        key=lambda item: (
            item["expected_profit_jpy"],
            item["expected_profit_pct"],
            -item["total_cost_jpy"],
        ),
        reverse=True,
    )

    for idx, item in enumerate(payloads, start=1):
        item["arbitrage_rank"] = idx
    if payloads:
        payloads[0]["is_recommended"] = True
        best_risk = min(
            payloads,
            key=lambda item: (
                int(item["risk_adjusted_total_cost_jpy"]),
                -int(item["expected_profit_jpy"]),
                str(item["proxy_name"]),
            ),
        )
        best_risk["is_recommended_risk_adjusted"] = True

    return payloads


def upsert_proxy_deals(
    session: Session,
    listing_id: str,
    payloads: list[dict],
) -> list[ProxyOptionEstimate]:
    existing_rows = session.scalars(
        select(ProxyOptionEstimate).where(ProxyOptionEstimate.listing_id == listing_id)
    ).all()
    existing_by_name = {row.proxy_name: row for row in existing_rows}

    rows: list[ProxyOptionEstimate] = []
    incoming_names = {payload["proxy_name"] for payload in payloads}
    for payload in payloads:
        row = existing_by_name.get(payload["proxy_name"])
        if row is None:
            row = ProxyOptionEstimate(listing_id=listing_id, proxy_name=payload["proxy_name"])

        row.total_cost_jpy = payload["total_cost_jpy"]
        row.resale_reference_jpy = payload["resale_reference_jpy"]
        row.expected_profit_jpy = payload["expected_profit_jpy"]
        row.expected_profit_pct = payload["expected_profit_pct"]
        row.arbitrage_rank = payload["arbitrage_rank"]
        row.coupon_id = payload["coupon_id"]
        row.coupon_discount_jpy = payload["coupon_discount_jpy"]
        row.cost_confidence = payload["cost_confidence"]
        row.is_recommended = payload["is_recommended"]
        session.add(row)
        rows.append(row)

    for row in existing_rows:
        if row.proxy_name not in incoming_names:
            session.delete(row)

    session.flush()
    return rows


def get_proxy_deals_for_listing(session: Session, listing_id: str) -> list[ProxyOptionEstimate]:
    return session.scalars(
        select(ProxyOptionEstimate)
        .where(ProxyOptionEstimate.listing_id == listing_id)
        .order_by(ProxyOptionEstimate.arbitrage_rank.asc(), ProxyOptionEstimate.total_cost_jpy.asc())
    ).all()


def get_top_proxy_deals(
    session: Session,
    proxy_name: str | None,
    limit: int,
) -> list[tuple[ProxyOptionEstimate, RawListing]]:
    stmt = (
        select(ProxyOptionEstimate, RawListing)
        .join(RawListing, RawListing.listing_id == ProxyOptionEstimate.listing_id)
        .order_by(ProxyOptionEstimate.expected_profit_jpy.desc())
        .limit(limit)
    )
    if proxy_name:
        stmt = stmt.where(ProxyOptionEstimate.proxy_name == proxy_name)

    return list(session.execute(stmt).all())


def proxy_option_diagnostics(listing: RawListing, row: ProxyOptionEstimate) -> dict:
    compatible = _is_proxy_compatible(row.proxy_name, listing.source)
    first_time_penalty = _first_time_penalty_for_proxy(listing, row.proxy_name)
    adjusted_total = int(max(0, int(row.total_cost_jpy) + int(first_time_penalty)))
    confidence = max(0.45, float(row.cost_confidence or 0.0))
    risk_adjusted_total_cost = int(math.ceil(adjusted_total / confidence))

    return {
        "compatible_with_marketplace": bool(compatible),
        "compatibility_note": (
            None
            if compatible
            else f"{row.proxy_name} is not configured for source={listing.source}"
        ),
        "first_time_penalty_jpy": int(first_time_penalty),
        "risk_adjusted_total_cost_jpy": int(risk_adjusted_total_cost),
    }
