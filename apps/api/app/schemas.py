from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DealBucket = Literal["confident", "potential", "discard"]
ListingType = Literal["auction", "buy_now"]
ReviewAction = Literal[
    "confirm_classification",
    "correct_classification",
    "mark_fake_suspicious",
    "mark_condition_worse",
    "mark_purchased",
    "mark_sold_too_fast",
    "mark_not_worth_it",
]


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
    source_counts: dict[str, int] = Field(default_factory=dict)
    report_path: str | None = None


class EndingAuctionRefreshResponse(BaseModel):
    started_at: datetime
    finished_at: datetime
    ingested_count: int
    scored_count: int
    window_hours: int


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


class ProxyDealOption(BaseModel):
    listing_id: str
    marketplace: str
    listing_title: str
    proxy_name: str
    arbitrage_rank: int | None = None
    total_cost_jpy: int
    resale_reference_jpy: int
    expected_profit_jpy: int
    expected_profit_pct: float
    coupon_id: str | None = None
    coupon_discount_jpy: int
    is_recommended: bool


class ProxyDealsForListingResponse(BaseModel):
    listing_id: str
    options: list[ProxyDealOption]


class ProxyTopDealsResponse(BaseModel):
    total: int
    items: list[ProxyDealOption]


class ManualReviewRequest(BaseModel):
    action_type: ReviewAction
    corrected_classification_id: str | None = None
    corrected_condition_grade: str | None = None
    is_false_positive: bool = False
    was_purchased: bool = False
    notes: str = ""
    reviewer: str = "self"


class ManualReviewResponse(BaseModel):
    review_id: str
    training_example_id: str
    listing_id: str
    action_type: ReviewAction
    created_at: datetime


class RetrainJobResponse(BaseModel):
    started_at: datetime
    finished_at: datetime
    status: Literal["ok", "error"]
    details: str