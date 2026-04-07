import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClassificationResult, ManualReview, RawListing, TrainingExample


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def create_manual_review(
    session: Session,
    listing_id: str,
    action_type: str,
    corrected_classification_id: str | None,
    corrected_condition_grade: str | None,
    is_false_positive: bool,
    was_purchased: bool,
    notes: str,
    reviewer: str,
) -> tuple[ManualReview, TrainingExample]:
    listing = session.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        raise LookupError(f"Listing {listing_id} not found")

    classification = session.scalar(
        select(ClassificationResult).where(ClassificationResult.listing_id == listing_id)
    )

    review = ManualReview(
        listing_id=listing_id,
        action_type=action_type,
        corrected_classification_id=corrected_classification_id,
        corrected_condition_grade=corrected_condition_grade,
        is_false_positive=is_false_positive,
        was_purchased=was_purchased,
        notes=notes,
        reviewer=reviewer,
    )
    session.add(review)
    session.flush()

    training_example = TrainingExample(
        listing_id=listing_id,
        source_review_id=review.review_id,
        task_type="manual_feedback",
        label_json=_to_json(
            {
                "action_type": action_type,
                "corrected_classification_id": corrected_classification_id,
                "corrected_condition_grade": corrected_condition_grade,
                "is_false_positive": is_false_positive,
                "was_purchased": was_purchased,
            }
        ),
        feature_json=_to_json(
            {
                "source": listing.source,
                "title": listing.title,
                "description_raw": listing.description_raw,
                "condition_text": listing.condition_text,
                "listing_format": listing.listing_format,
                "existing_classification_id": (
                    classification.classification_id if classification else None
                ),
            }
        ),
        split="train",
    )
    session.add(training_example)
    session.flush()

    return review, training_example
