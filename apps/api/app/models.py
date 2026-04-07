from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


class RawListing(Base):
    __tablename__ = "raw_listing"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_raw_listing_source_id"),
    )

    listing_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_listing_id: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512))
    description_raw: Mapped[str] = mapped_column(Text, default="")
    images_json: Mapped[str] = mapped_column(Text, default="[]")
    seller_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seller_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    listing_format: Mapped[str] = mapped_column(String(32), default="buy_now")
    current_price_jpy: Mapped[int] = mapped_column(Integer, default=0)
    price_buy_now_jpy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domestic_shipping_jpy: Mapped[int] = mapped_column(Integer, default=0)
    bid_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location_prefecture: Mapped[str | None] = mapped_column(String(128), nullable=True)
    condition_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lot_size_hint: Mapped[int] = mapped_column(Integer, default=1)
    raw_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ClassificationResult(Base):
    __tablename__ = "classification_result"

    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        primary_key=True,
    )
    classification_id: Mapped[str] = mapped_column(String(255), index=True)
    brand: Mapped[str] = mapped_column(String(64))
    line: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nib_material: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nib_size: Mapped[str | None] = mapped_column(String(32), nullable=True)
    condition_grade: Mapped[str] = mapped_column(String(32), default="unknown")
    condition_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    item_count_estimate: Mapped[int] = mapped_column(Integer, default=1)
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    condition_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    lot_decomposition_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    text_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ValuationPrediction(Base):
    __tablename__ = "valuation_prediction"

    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        primary_key=True,
    )
    resale_pred_jpy: Mapped[int] = mapped_column(Integer)
    resale_ci_low_jpy: Mapped[int] = mapped_column(Integer)
    resale_ci_high_jpy: Mapped[int] = mapped_column(Integer)
    valuation_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class AuctionPrediction(Base):
    __tablename__ = "auction_prediction"

    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        primary_key=True,
    )
    auction_low_win_price_jpy: Mapped[int] = mapped_column(Integer)
    auction_expected_final_price_jpy: Mapped[int] = mapped_column(Integer)
    auction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ProxyOptionEstimate(Base):
    __tablename__ = "proxy_option_estimate"
    __table_args__ = (
        UniqueConstraint("listing_id", "proxy_name", name="uq_proxy_option_per_listing"),
    )

    estimate_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    proxy_name: Mapped[str] = mapped_column(String(64), index=True)
    total_cost_jpy: Mapped[int] = mapped_column(Integer)
    resale_reference_jpy: Mapped[int] = mapped_column(Integer, default=0)
    expected_profit_jpy: Mapped[int] = mapped_column(Integer, default=0)
    expected_profit_pct: Mapped[float] = mapped_column(Float, default=0.0)
    arbitrage_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coupon_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    coupon_discount_jpy: Mapped[int] = mapped_column(Integer, default=0)
    cost_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ProxyPricingPolicy(Base):
    __tablename__ = "proxy_pricing_policy"

    policy_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proxy_name: Mapped[str] = mapped_column(String(64), index=True)
    marketplace_source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    service_fee_jpy: Mapped[int] = mapped_column(Integer, default=0)
    intl_shipping_jpy: Mapped[int] = mapped_column(Integer, default=0)
    min_buy_price_jpy: Mapped[int] = mapped_column(Integer, default=0)
    max_buy_price_jpy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class CouponRule(Base):
    __tablename__ = "coupon_rule"

    coupon_rule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    proxy_name: Mapped[str] = mapped_column(String(64), index=True)
    marketplace_source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    coupon_id: Mapped[str] = mapped_column(String(128), index=True)
    discount_type: Mapped[str] = mapped_column(String(32), default="flat_jpy")
    discount_value: Mapped[float] = mapped_column(Float, default=0.0)
    min_buy_price_jpy: Mapped[int] = mapped_column(Integer, default=0)
    max_discount_jpy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_stackable: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshot"
    __table_args__ = (
        UniqueConstraint("listing_id", "snapshot_hash", name="uq_listing_snapshot_hash"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    current_price_jpy: Mapped[int] = mapped_column(Integer, default=0)
    price_buy_now_jpy: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bid_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class ListingImage(Base):
    __tablename__ = "listing_image"
    __table_args__ = (
        UniqueConstraint("listing_id", "image_url", name="uq_listing_image_url"),
    )

    image_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    image_url: Mapped[str] = mapped_column(String(2048))
    image_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DealScore(Base):
    __tablename__ = "deal_score"

    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        primary_key=True,
    )
    expected_profit_jpy: Mapped[int] = mapped_column(Integer)
    expected_profit_pct: Mapped[float] = mapped_column(Float)
    risk_adjusted_profit_jpy: Mapped[int] = mapped_column(Integer)
    confidence_overall: Mapped[float] = mapped_column(Float, default=0.0)
    bucket: Mapped[str] = mapped_column(String(16), default="discard", index=True)
    risk_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ReportRun(Base):
    __tablename__ = "report_run"

    report_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ReportItem(Base):
    __tablename__ = "report_item"
    __table_args__ = (
        UniqueConstraint("report_run_id", "listing_id", name="uq_report_listing"),
    )

    report_item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("report_run.report_run_id", ondelete="CASCADE"),
        index=True,
    )
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    bucket: Mapped[str] = mapped_column(String(16), index=True)
    rank_position: Mapped[int] = mapped_column(Integer)


class ManualReview(Base):
    __tablename__ = "manual_review"

    review_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    corrected_classification_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    corrected_condition_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_false_positive: Mapped[bool] = mapped_column(Boolean, default=False)
    was_purchased: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(64), default="self")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class TrainingExample(Base):
    __tablename__ = "training_example"

    example_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_listing.listing_id", ondelete="CASCADE"),
        index=True,
    )
    source_review_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("manual_review.review_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(String(64), default="classification", index=True)
    label_json: Mapped[str] = mapped_column(Text, default="{}")
    feature_json: Mapped[str] = mapped_column(Text, default="{}")
    split: Mapped[str] = mapped_column(String(16), default="train")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
