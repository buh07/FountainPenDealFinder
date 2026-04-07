from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DealBucket = Literal["confident", "potential", "discard"]
ListingType = Literal["auction", "buy_now"]


class ListingItem(BaseModel):
    item_index: int
    classification_id: str
    condition_grade: str | None = None
    condition_flags: list[str] = Field(default_factory=list)
    visibility_confidence: float | None = None


class ListingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listing_id: str
    classification: str
    condition_summary: str
    item_count_estimate: int
    items: list[ListingItem] = Field(default_factory=list)
    marketplace: str
    listing_title: str
    listing_url: str
    seller_id: str | None = None
    listing_type: ListingType
    current_price_jpy: int = Field(ge=0)
    estimated_total_buy_cost_jpy: int = Field(ge=0)
    estimated_resale_price_jpy: int = Field(ge=0)
    expected_profit_jpy: int
    expected_profit_pct: float
    confidence: float = Field(ge=0.0, le=1.0)
    auction_low_win_price_jpy: int | None = None
    auction_expected_final_price_jpy: int | None = None
    recommended_proxy: str = "None"
    deal_bucket: DealBucket
    listed_at: datetime | None = None
    time_remaining: str | None = None
    rationale: str


class ListListingsResponse(BaseModel):
    total: int
    items: list[ListingSummary]


class CollectRunRequest(BaseModel):
    report_date: date | None = None


class CollectRunResponse(BaseModel):
    started_at: datetime
    finished_at: datetime
    ingested_count: int
    scored_count: int
    confident_count: int
    potential_count: int
    report_path: str | None = None


class DailyReportResponse(BaseModel):
    date: date
    generated_at: datetime
    report_path: str | None = None
    confident: list[ListingSummary]
    potential: list[ListingSummary]


class ResalePredictionResponse(BaseModel):
    listing_id: str
    predicted_resale_price_jpy: int
    p10_resale_price_jpy: int
    p90_resale_price_jpy: int
    valuation_confidence: float


class AuctionPredictionResponse(BaseModel):
    listing_id: str
    expected_final_price_jpy: int | None = None
    low_tail_price_jpy: int | None = None
    auction_confidence: float | None = None