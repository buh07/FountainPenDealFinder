from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ProxyOptionEstimate, RawListing


PROXY_RULES = [
    {
        "name": "Buyee",
        "service_fee_jpy": 300,
        "coupon_sources": {"rakuma", "yahoo_flea_market"},
        "intl_shipping_jpy": 1800,
    },
    {
        "name": "FromJapan",
        "service_fee_jpy": 250,
        "coupon_sources": {"mercari", "yahoo_auctions"},
        "intl_shipping_jpy": 1800,
    },
    {
        "name": "Neokyo",
        "service_fee_jpy": 275,
        "coupon_sources": set(),
        "intl_shipping_jpy": 1850,
    },
]


def estimate_proxy_deals(
    listing: RawListing,
    buy_price_jpy: int,
    resale_reference_jpy: int,
) -> list[dict]:
    domestic_shipping = listing.domestic_shipping_jpy or 1200

    payloads: list[dict] = []
    for rule in PROXY_RULES:
        coupon_discount = 0
        coupon_id = None

        if listing.source in rule["coupon_sources"]:
            coupon_discount = int(rule["service_fee_jpy"] * 0.5)
            coupon_id = f"{rule['name'].lower()}_servicefee50"

        if buy_price_jpy >= 60000 and rule["name"] == "FromJapan":
            coupon_discount += 500
            coupon_id = coupon_id or "fromjapan_item500"

        total_cost = max(
            0,
            buy_price_jpy
            + domestic_shipping
            + rule["service_fee_jpy"]
            + rule["intl_shipping_jpy"]
            - coupon_discount,
        )

        expected_profit = resale_reference_jpy - total_cost
        expected_profit_pct = expected_profit / max(total_cost, 1)

        payloads.append(
            {
                "proxy_name": rule["name"],
                "total_cost_jpy": int(total_cost),
                "resale_reference_jpy": int(resale_reference_jpy),
                "expected_profit_jpy": int(expected_profit),
                "expected_profit_pct": round(expected_profit_pct, 4),
                "coupon_id": coupon_id,
                "coupon_discount_jpy": int(coupon_discount),
                "cost_confidence": 0.74 if coupon_discount == 0 else 0.66,
                "is_recommended": False,
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
