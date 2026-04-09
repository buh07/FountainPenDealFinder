from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ListListingsResponse, ListingSummary
from ..services.reporting import count_ranked_listings, get_listing_summary, list_ranked_listings

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=ListListingsResponse)
def get_listings(
    source: str | None = Query(default=None),
    bucket: str | None = Query(default=None, pattern="^(confident|potential|discard)?$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    db: Session = Depends(get_db),
) -> ListListingsResponse:
    total = count_ranked_listings(db, source=source, bucket=bucket)
    items = list_ranked_listings(db, source=source, bucket=bucket, limit=limit, offset=offset)
    return ListListingsResponse(total=total, items=items)


@router.get("/{listing_id}", response_model=ListingSummary)
def get_listing_by_id(listing_id: str, db: Session = Depends(get_db)) -> ListingSummary:
    summary = get_listing_summary(db, listing_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return summary
