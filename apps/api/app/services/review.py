import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import ClassificationResult, ManualReview, RawListing, TrainingExample
from .taxonomy import (
    add_taxonomy_feedback_type,
    canonicalize_condition_grade,
    resolve_taxonomy,
)

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
    logger.warning("fcntl is unavailable; feedback JSONL appends are not process-safe on this platform")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _feedback_pricing_path() -> Path:
    settings = get_settings()
    root = _repo_root()
    path = Path(settings.feedback_pricing_labels_path)
    if not path.is_absolute():
        path = root / path
    return path


def _append_feedback_pricing_row(payload: dict[str, Any]) -> None:
    path = _feedback_pricing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def create_manual_review(
    session: Session,
    listing_id: str,
    action_type: str,
    corrected_classification_id: str | None,
    corrected_brand: str | None,
    corrected_line: str | None,
    corrected_condition_grade: str | None,
    corrected_item_count: int | None,
    corrected_ask_price_jpy: int | None,
    corrected_sold_price_jpy: int | None,
    taxonomy_aliases: list[str],
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

    base_brand = corrected_brand or (classification.brand if classification else None)
    base_line = corrected_line or (classification.line if classification else None)
    taxonomy = resolve_taxonomy(
        brand=base_brand,
        line=base_line,
        classification_id=corrected_classification_id,
        text=" ".join(
            part
            for part in [
                listing.title,
                listing.description_raw or "",
                listing.condition_text or "",
            ]
            if part
        ),
    )

    canonical_condition = canonicalize_condition_grade(
        corrected_condition_grade
        or (classification.condition_grade if classification else None)
        or "B"
    )

    canonical_classification_id = str(taxonomy["classification_id"] or "unknown_fountain_pen")

    review = ManualReview(
        listing_id=listing_id,
        action_type=action_type,
        corrected_classification_id=canonical_classification_id,
        corrected_condition_grade=canonical_condition,
        is_false_positive=is_false_positive,
        was_purchased=was_purchased,
        notes=notes,
        reviewer=reviewer,
    )
    session.add(review)
    session.flush()

    corrected_item_count_final = corrected_item_count or (classification.item_count_estimate if classification else 1)

    training_example = TrainingExample(
        listing_id=listing_id,
        source_review_id=review.review_id,
        task_type="manual_feedback",
        label_json=_to_json(
            {
                "action_type": action_type,
                "canonical_category": taxonomy["category"],
                "canonical_brand": taxonomy["brand"],
                "canonical_line": taxonomy["line"],
                "canonical_classification_id": canonical_classification_id,
                "canonical_condition_grade": canonical_condition,
                "corrected_item_count": int(corrected_item_count_final or 1),
                "corrected_ask_price_jpy": int(corrected_ask_price_jpy or 0),
                "corrected_sold_price_jpy": int(corrected_sold_price_jpy or 0),
                "taxonomy_aliases": [
                    str(alias).strip() for alias in taxonomy_aliases if str(alias).strip()
                ],
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

    cleaned_aliases = sorted({str(alias).strip() for alias in taxonomy_aliases if str(alias).strip()})
    if cleaned_aliases and taxonomy["brand"] and taxonomy["line"]:
        add_taxonomy_feedback_type(
            brand=str(taxonomy["brand"]),
            line=str(taxonomy["line"]),
            aliases=cleaned_aliases,
            source_review_id=review.review_id,
            reviewer=reviewer,
        )

    if int(corrected_ask_price_jpy or 0) > 0 and int(corrected_sold_price_jpy or 0) > 0:
        _append_feedback_pricing_row(
            {
                "source": "manual_feedback",
                "review_id": review.review_id,
                "brand": taxonomy["brand"],
                "line": taxonomy["line"] or "fountain_pen",
                "category": taxonomy["category"],
                "classification_id": canonical_classification_id,
                "condition_grade": canonical_condition,
                "item_count": int(corrected_item_count_final or 1),
                "ask_price_jpy": int(corrected_ask_price_jpy),
                "sold_price_jpy": int(corrected_sold_price_jpy),
                "sold_at": datetime.now(timezone.utc).isoformat(),
                "notes": notes,
            }
        )

    return review, training_example
