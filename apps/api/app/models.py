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
