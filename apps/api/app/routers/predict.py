from sqlalchemy import select
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import RawListing
from ..schemas import AuctionPredictionResponse, ResalePredictionResponse
from ..services.pipeline import predict_auction_for_listing, predict_resale_for_listing

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("/resale/{listing_id}", response_model=ResalePredictionResponse)
def predict_resale(listing_id: str, db: Session = Depends(get_db)) -> ResalePredictionResponse:
    prediction = predict_resale_for_listing(db, listing_id)
    if prediction is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    return ResalePredictionResponse(
        listing_id=listing_id,
        predicted_resale_price_jpy=prediction.resale_pred_jpy,
        p10_resale_price_jpy=prediction.resale_ci_low_jpy,
        p90_resale_price_jpy=prediction.resale_ci_high_jpy,
        valuation_confidence=prediction.valuation_confidence,
    )


@router.post("/auction/{listing_id}", response_model=AuctionPredictionResponse)
def predict_auction(listing_id: str, db: Session = Depends(get_db)) -> AuctionPredictionResponse:
    listing_exists = db.scalar(
        select(RawListing.listing_id).where(RawListing.listing_id == listing_id)
    )
    if listing_exists is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    prediction = predict_auction_for_listing(db, listing_id)
    if prediction is None:
        return AuctionPredictionResponse(
            listing_id=listing_id,
            expected_final_price_jpy=None,
            low_tail_price_jpy=None,
            auction_confidence=None,
        )

    return AuctionPredictionResponse(
        listing_id=listing_id,
        expected_final_price_jpy=(
            prediction.auction_expected_final_price_jpy if prediction else None
        ),
        low_tail_price_jpy=(prediction.auction_low_win_price_jpy if prediction else None),
        auction_confidence=(prediction.auction_confidence if prediction else None),
    )
