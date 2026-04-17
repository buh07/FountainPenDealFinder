import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ListingAsset, RawListing
from ..schemas import ListListingsResponse, ListingImagesResponse, ListingSummary
from ..services.reporting import count_ranked_listings, get_listing_summary, list_ranked_listings

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=ListListingsResponse)
def get_listings(
    source: str | None = Query(default=None),
    bucket: str | None = Query(default=None, pattern="^(confident|potential|discard)?$"),
    sort_by: Literal["risk_adjusted", "flat_profit", "percent_profit"] = Query(
        default="risk_adjusted"
    ),
    listing_type: Literal["auction", "buy_now"] | None = Query(default=None),
    since_hours: int | None = Query(default=None, ge=1, le=24 * 14),
    ending_within_hours: int | None = Query(default=None, ge=1, le=72),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    db: Session = Depends(get_db),
) -> ListListingsResponse:
    since = None
    if since_hours is not None:
        since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    total = count_ranked_listings(
        db,
        source=source,
        bucket=bucket,
        listing_type=listing_type,
        since=since,
        ending_within_hours=ending_within_hours,
    )
    items = list_ranked_listings(
        db,
        source=source,
        bucket=bucket,
        sort_by=sort_by,
        listing_type=listing_type,
        since=since,
        ending_within_hours=ending_within_hours,
        limit=limit,
        offset=offset,
    )
    return ListListingsResponse(total=total, items=items)


@router.get("/{listing_id}", response_model=ListingSummary)
def get_listing_by_id(listing_id: str, db: Session = Depends(get_db)) -> ListingSummary:
    summary = get_listing_summary(db, listing_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return summary


@router.get("/{listing_id}/images", response_model=ListingImagesResponse)
def get_listing_images(
    listing_id: str,
    include_assets: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> ListingImagesResponse:
    listing = db.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    image_urls: list[str] = []
    raw_images = str(listing.images_json or "").strip()
    if raw_images:
        try:
            parsed = json.loads(raw_images)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            image_urls = [str(item).strip() for item in parsed if str(item).strip()]

    captured_assets: list[str] = []
    if include_assets:
        assets = db.scalars(
            select(ListingAsset)
            .where(
                ListingAsset.listing_id == listing_id,
                ListingAsset.asset_type.in_(["image", "thumbnail"]),
            )
            .order_by(ListingAsset.created_at.asc())
        ).all()
        captured_assets = [asset.local_path for asset in assets if asset.local_path]

    return ListingImagesResponse(
        listing_id=listing_id,
        image_urls=image_urls,
        captured_assets=captured_assets,
    )
