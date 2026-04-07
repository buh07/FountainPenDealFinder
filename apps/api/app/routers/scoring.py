from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ListingSummary
from ..services.pipeline import rescore_listing
from ..services.reporting import get_listing_summary

router = APIRouter(prefix="/score", tags=["score"])


@router.post("/{listing_id}", response_model=ListingSummary)
def score_listing(listing_id: str, db: Session = Depends(get_db)) -> ListingSummary:
    rescore_listing(db, listing_id)
    summary = get_listing_summary(db, listing_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return summary
