from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import RawListing
from ..schemas import ProxyDealOption, ProxyDealsForListingResponse, ProxyTopDealsResponse
from ..services.proxy_tracker import get_proxy_deals_for_listing, get_top_proxy_deals

router = APIRouter(prefix="/proxy", tags=["proxy"])


def _to_option(row, listing: RawListing) -> ProxyDealOption:
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
    )


@router.get("/listing/{listing_id}", response_model=ProxyDealsForListingResponse)
def listing_proxy_deals(listing_id: str, db: Session = Depends(get_db)) -> ProxyDealsForListingResponse:
    listing = db.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    rows = get_proxy_deals_for_listing(db, listing_id)
    return ProxyDealsForListingResponse(
        listing_id=listing_id,
        options=[_to_option(row, listing) for row in rows],
    )


@router.get("/top", response_model=ProxyTopDealsResponse)
def top_proxy_deals(
    proxy_name: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ProxyTopDealsResponse:
    rows = get_top_proxy_deals(db, proxy_name=proxy_name, limit=limit)
    items = [_to_option(proxy_row, listing) for proxy_row, listing in rows]
    return ProxyTopDealsResponse(total=len(items), items=items)
