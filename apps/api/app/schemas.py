from datetime import date, datetime
from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DealBucket = Literal["confident", "potential", "discard"]
ListingType = Literal["auction", "buy_now"]
PriceStatus = Literal["valid", "missing", "parse_error"]
ModelTask = Literal["resale", "auction"]
ReviewAction = Literal[
    "confirm_classification",
    "correct_classification",
    "add_new_type",
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
    image_urls: list[str] = Field(default_factory=list)
    seller_id: str | None = None
    listing_type: ListingType
    price_status: PriceStatus = "valid"
    current_price_jpy: int = Field(ge=0)
    estimated_total_buy_cost_jpy: int = Field(ge=0)
    estimated_resale_price_jpy: int = Field(ge=0)
    expected_profit_jpy: int
    expected_profit_pct: float
    confidence: float = Field(ge=0.0, le=1.0)
    classification_confidence: float | None = None
    condition_confidence: float | None = None
    lot_decomposition_confidence: float | None = None
    valuation_confidence: float | None = None
    auction_confidence: float | None = None
    cost_confidence: float | None = None
    stage_explanations: dict[str, Any] = Field(default_factory=dict)
    auction_low_win_price_jpy: int | None = None
    auction_expected_final_price_jpy: int | None = None
    recommended_proxy: str = "None"
    deal_bucket: DealBucket
    risk_flags: list[str] = Field(default_factory=list)
    listed_at: datetime | None = None
    time_remaining: str | None = None
    rationale: str


class ListListingsResponse(BaseModel):
    total: int
    items: list[ListingSummary]


class ListingImagesResponse(BaseModel):
    listing_id: str
    image_urls: list[str] = Field(default_factory=list)
    captured_assets: list[str] = Field(default_factory=list)


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


class PriorityAuctionRefreshResponse(BaseModel):
    started_at: datetime
    finished_at: datetime
    candidate_count: int
    ingested_count: int
    scored_count: int
    window_hours: int
    threshold: float


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
    is_recommended_by_risk_adjusted_cost: bool = False
    risk_adjusted_total_cost_jpy: int
    first_time_penalty_jpy: int = 0
    compatible_with_marketplace: bool = True
    compatibility_note: str | None = None


class ProxyDealsForListingResponse(BaseModel):
    listing_id: str
    recommended_proxy_by_expected_profit: str | None = None
    best_proxy_by_risk_adjusted_cost: str | None = None
    options: list[ProxyDealOption]


class ProxyTopDealsResponse(BaseModel):
    total: int
    items: list[ProxyDealOption]


class ManualReviewRequest(BaseModel):
    action_type: ReviewAction
    corrected_classification_id: str | None = None
    corrected_brand: str | None = None
    corrected_line: str | None = None
    corrected_condition_grade: str | None = None
    corrected_item_count: int | None = Field(default=None, ge=1)
    corrected_ask_price_jpy: int | None = Field(default=None, ge=0)
    corrected_sold_price_jpy: int | None = Field(default=None, ge=0)
    taxonomy_aliases: list[str] = Field(default_factory=list)
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


class ModelVersionInfo(BaseModel):
    task: ModelTask
    version_id: str
    artifact_path: str
    created_at: datetime
    is_active: bool = False


class ActiveModelVersionResponse(BaseModel):
    task: ModelTask
    active: ModelVersionInfo | None = None
    fallback_artifact_path: str


class ModelVersionListResponse(BaseModel):
    task: ModelTask
    active_version_id: str | None = None
    versions: list[ModelVersionInfo] = Field(default_factory=list)


class ModelRollbackRequest(BaseModel):
    version_id: str


class ModelRollbackResponse(BaseModel):
    task: ModelTask
    previous_version_id: str | None = None
    active: ModelVersionInfo | None = None


class HealthMetricsResponse(BaseModel):
    generated_at: datetime
    window_hours: int
    total_recent_listings: int
    source_counts: dict[str, int] = Field(default_factory=dict)
    parse_completeness_avg: float
    non_discard_rate: float
    manual_review_count: int
    false_positive_rate: float | None = None
    baseline_eval_pass: bool | None = None
    ingestion_failure_count: int = 0
    latest_ingestion_failure_reason: str | None = None
    retrain_failure_count: int = 0
    latest_retrain_failure_reason: str | None = None
    active_model_versions: dict[str, str | None] = Field(default_factory=dict)
    model_age_hours: dict[str, float | None] = Field(default_factory=dict)
    recent_non_stale_listing_count: int = 0
    latest_non_stale_listing_at: datetime | None = None
    listing_freshness_hours: float | None = None
    alerts: list[str] = Field(default_factory=list)


class HealthAlertDispatchResponse(BaseModel):
    sent: bool
    reason: str
    alert_count: int
    destination: str | None = None
    status_code: int | None = None
    deduped: bool = False
    cooldown_remaining_seconds: int | None = None
    alert_signature: str | None = None


class TaxonomyTypeEntry(BaseModel):
    brand: str
    line: str
    category: str
    aliases: list[str] = Field(default_factory=list)


class TaxonomyStandardResponse(BaseModel):
    categories: dict[str, list[str]] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    condition_taxonomy: list[dict[str, str]] = Field(default_factory=list)
    damage_flag_taxonomy: list[str] = Field(default_factory=list)
    types: list[TaxonomyTypeEntry] = Field(default_factory=list)
