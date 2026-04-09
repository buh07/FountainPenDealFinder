from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ManualReviewRequest, ManualReviewResponse
from ..services.review import create_manual_review

router = APIRouter(prefix="/review", tags=["review"])


@router.post("/{listing_id}", response_model=ManualReviewResponse)
def review_listing(
    listing_id: str,
    payload: ManualReviewRequest,
    db: Session = Depends(get_db),
) -> ManualReviewResponse:
    try:
        review, training_example = create_manual_review(
            db,
            listing_id=listing_id,
            action_type=payload.action_type,
            corrected_classification_id=payload.corrected_classification_id,
            corrected_brand=payload.corrected_brand,
            corrected_line=payload.corrected_line,
            corrected_condition_grade=payload.corrected_condition_grade,
            corrected_item_count=payload.corrected_item_count,
            corrected_ask_price_jpy=payload.corrected_ask_price_jpy,
            corrected_sold_price_jpy=payload.corrected_sold_price_jpy,
            taxonomy_aliases=payload.taxonomy_aliases,
            is_false_positive=payload.is_false_positive,
            was_purchased=payload.was_purchased,
            notes=payload.notes,
            reviewer=payload.reviewer,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    db.commit()

    return ManualReviewResponse(
        review_id=review.review_id,
        training_example_id=training_example.example_id,
        listing_id=review.listing_id,
        action_type=review.action_type,
        created_at=review.created_at,
    )
