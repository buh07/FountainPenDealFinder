from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DealBucket = Literal["confident", "potential"]
ListingType = Literal["auction", "buy_now"]


class ListingSummary(BaseModel):
    listing_id: str
    marketplace: str
    listing_title: str
    listing_url: str
    listing_type: ListingType
    current_price_jpy: int = Field(ge=0)
    estimated_total_buy_cost_jpy: int = Field(ge=0)
    estimated_resale_price_jpy: int = Field(ge=0)
    expected_profit_jpy: int
    expected_profit_pct: float
    confidence: float = Field(ge=0.0, le=1.0)
    deal_bucket: DealBucket
    listed_at: datetime | None = None
    time_remaining: str | None = None
    rationale: str