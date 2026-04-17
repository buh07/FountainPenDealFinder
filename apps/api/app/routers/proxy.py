from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ProxyOptionEstimate, RawListing
from ..schemas import ProxyDealOption, ProxyDealsForListingResponse, ProxyTopDealsResponse
from ..services.proxy_tracker import (
    get_proxy_deals_for_listing,
    get_top_proxy_deals,
    proxy_option_diagnostics,
)

router = APIRouter(prefix="/proxy", tags=["proxy"])


def _to_option(
    row,
    listing: RawListing,
    *,
    is_recommended_by_risk_adjusted_cost: bool = False,
) -> ProxyDealOption:
    diagnostics = proxy_option_diagnostics(listing, row)
    return ProxyDealOption(
        listing_id=row.listing_id,
        marketplace=listing.source,
        listing_title=listing.title,
        proxy_name=row.proxy_name,
        arbitrage_rank=row.arbitrage_rank,
        total_cost_jpy=row.total_cost_jpy,
        resale_reference_jpy=row.resale_reference_jpy,
        expected_profit_jpy=row.expected_profit_jpy,
        expected_profit_pct=row.expected_profit_pct,
        coupon_id=row.coupon_id,
        coupon_discount_jpy=row.coupon_discount_jpy,
        is_recommended=row.is_recommended,
        is_recommended_by_risk_adjusted_cost=is_recommended_by_risk_adjusted_cost,
        risk_adjusted_total_cost_jpy=diagnostics["risk_adjusted_total_cost_jpy"],
        first_time_penalty_jpy=diagnostics["first_time_penalty_jpy"],
        compatible_with_marketplace=diagnostics["compatible_with_marketplace"],
        compatibility_note=diagnostics["compatibility_note"],
    )


@router.get("/listing/{listing_id}", response_model=ProxyDealsForListingResponse)
def listing_proxy_deals(listing_id: str, db: Session = Depends(get_db)) -> ProxyDealsForListingResponse:
    listing = db.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    rows = get_proxy_deals_for_listing(db, listing_id)
    options = [_to_option(row, listing) for row in rows]
    best_risk_proxy = None
    if options:
        best_risk = min(
            options,
            key=lambda option: (
                option.risk_adjusted_total_cost_jpy,
                -option.expected_profit_jpy,
                option.proxy_name,
            ),
        )
        best_risk.is_recommended_by_risk_adjusted_cost = True
        best_risk_proxy = best_risk.proxy_name

    recommended_proxy = next((option.proxy_name for option in options if option.is_recommended), None)
    return ProxyDealsForListingResponse(
        listing_id=listing_id,
        recommended_proxy_by_expected_profit=recommended_proxy,
        best_proxy_by_risk_adjusted_cost=best_risk_proxy,
        options=options,
    )


@router.get("/top", response_model=ProxyTopDealsResponse)
def top_proxy_deals(
    proxy_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ProxyTopDealsResponse:
    rows = get_top_proxy_deals(db, proxy_name=proxy_name, limit=limit)
    listing_map = {listing.listing_id: listing for _proxy_row, listing in rows}
    listing_ids = list(listing_map.keys())

    best_risk_proxy_by_listing: dict[str, str] = {}
    best_risk_key_by_listing: dict[str, tuple[int, int, str]] = {}
    if listing_ids:
        all_rows = db.scalars(
            select(ProxyOptionEstimate)
            .where(ProxyOptionEstimate.listing_id.in_(listing_ids))
            .order_by(ProxyOptionEstimate.listing_id.asc(), ProxyOptionEstimate.proxy_name.asc())
        ).all()
        for row in all_rows:
            listing = listing_map.get(row.listing_id)
            if listing is None:
                continue
            diagnostics = proxy_option_diagnostics(listing, row)
            key = (
                diagnostics["risk_adjusted_total_cost_jpy"],
                -int(row.expected_profit_jpy or 0),
                str(row.proxy_name),
            )
            current_key = best_risk_key_by_listing.get(row.listing_id)
            if current_key is None or key < current_key:
                best_risk_proxy_by_listing[row.listing_id] = row.proxy_name
                best_risk_key_by_listing[row.listing_id] = key

    items = [
        _to_option(
            proxy_row,
            listing,
            is_recommended_by_risk_adjusted_cost=(
                best_risk_proxy_by_listing.get(listing.listing_id) == proxy_row.proxy_name
            ),
        )
        for proxy_row, listing in rows
    ]
    return ProxyTopDealsResponse(total=len(items), items=items)
