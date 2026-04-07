from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ListListingsResponse
from ..services.reporting import list_ranked_listings

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=ListListingsResponse)
def get_listings(
    source: str | None = Query(default=None),
    bucket: str | None = Query(default=None, pattern="^(confident|potential|discard)?$"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ListListingsResponse:
    items = list_ranked_listings(db, source=source, bucket=bucket, limit=limit)
    return ListListingsResponse(total=len(items), items=items)
