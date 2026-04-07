import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import (
    AuctionPrediction,
    ClassificationResult,
    DealScore,
    ProxyOptionEstimate,
    RawListing,
    ReportItem,
    ReportRun,
    ValuationPrediction,
)
from ..schemas import DailyReportResponse, ListingItem, ListingSummary


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _from_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _time_remaining(ends_at: datetime | None) -> str | None:
    if ends_at is None:
        return None

    now = datetime.now(timezone.utc)
    target = ends_at
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)

    delta = target - now
    if delta.total_seconds() <= 0:
        return "ended"

    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def get_listing_summary(session: Session, listing_id: str) -> ListingSummary | None:
    listing = session.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        return None

    classification = session.scalar(
        select(ClassificationResult).where(ClassificationResult.listing_id == listing_id)
    )
    valuation = session.scalar(
        select(ValuationPrediction).where(ValuationPrediction.listing_id == listing_id)
    )
    auction = session.scalar(select(AuctionPrediction).where(AuctionPrediction.listing_id == listing_id))
    deal = session.scalar(select(DealScore).where(DealScore.listing_id == listing_id))
    proxy = session.scalar(
        select(ProxyOptionEstimate).where(
            ProxyOptionEstimate.listing_id == listing_id,
            ProxyOptionEstimate.is_recommended.is_(True),
        )
    )

    condition_flags = _from_json(
        classification.condition_flags_json if classification else None,
        [],
    )
    condition_grade = classification.condition_grade if classification else "unknown"
    condition_summary = condition_grade
    if condition_flags:
        condition_summary = f"{condition_grade}: {', '.join(condition_flags)}"

    items_raw = _from_json(classification.items_json if classification else None, [])
    items = [ListingItem(**item) for item in items_raw if isinstance(item, dict)]

    default_total_cost = (
        (listing.price_buy_now_jpy or listing.current_price_jpy)
        + (listing.domestic_shipping_jpy or 0)
    )

    return ListingSummary(
        listing_id=listing.listing_id,
        classification=(
            classification.classification_id if classification else "unknown_fountain_pen"
        ),
        condition_summary=condition_summary,
        item_count_estimate=classification.item_count_estimate if classification else 1,
        items=items,
        marketplace=listing.source,
        listing_title=listing.title,
        listing_url=listing.url,
        seller_id=listing.seller_id,
        listing_type="auction" if listing.listing_format == "auction" else "buy_now",
        current_price_jpy=listing.current_price_jpy,
        estimated_total_buy_cost_jpy=proxy.total_cost_jpy if proxy else default_total_cost,
        estimated_resale_price_jpy=(valuation.resale_pred_jpy if valuation else 0),
        expected_profit_jpy=(deal.expected_profit_jpy if deal else 0),
        expected_profit_pct=(deal.expected_profit_pct if deal else 0.0),
        confidence=(deal.confidence_overall if deal else 0.0),
        auction_low_win_price_jpy=(
            auction.auction_low_win_price_jpy if auction else None
        ),
        auction_expected_final_price_jpy=(
            auction.auction_expected_final_price_jpy if auction else None
        ),
        recommended_proxy=(proxy.proxy_name if proxy else "None"),
        deal_bucket=(deal.bucket if deal else "discard"),
        listed_at=listing.listed_at,
        time_remaining=_time_remaining(listing.ends_at),
        rationale=(deal.rationale if deal else "Not scored yet."),
    )


def list_ranked_listings(
    session: Session,
    source: str | None = None,
    bucket: str | None = None,
    limit: int = 50,
) -> list[ListingSummary]:
    stmt = (
        select(RawListing.listing_id)
        .join(DealScore, DealScore.listing_id == RawListing.listing_id)
        .order_by(DealScore.risk_adjusted_profit_jpy.desc())
        .limit(limit)
    )

    if source:
        stmt = stmt.where(RawListing.source == source)

    if bucket:
        stmt = stmt.where(DealScore.bucket == bucket)
    else:
        stmt = stmt.where(DealScore.bucket.in_(["confident", "potential"]))

    listing_ids = [listing_id for listing_id in session.scalars(stmt).all()]

    results: list[ListingSummary] = []
    for listing_id in listing_ids:
        summary = get_listing_summary(session, listing_id)
        if summary is not None:
            results.append(summary)

    return results


def _render_markdown(
    report_date: date,
    generated_at: datetime,
    confident: list[ListingSummary],
    potential: list[ListingSummary],
) -> str:
    lines = [
        f"# FountainPenDealFinder Daily Report - {report_date.isoformat()}",
        "",
        f"Generated at: {generated_at.isoformat()}",
        "",
        "## Confident Good Deals",
        "",
    ]

    if not confident:
        lines.append("- No confident deals found.")
    else:
        for idx, item in enumerate(confident, start=1):
            lines.append(
                f"{idx}. {item.listing_title} | profit={item.expected_profit_jpy} JPY | "
                f"confidence={item.confidence:.2f} | proxy={item.recommended_proxy}"
            )
            lines.append(f"   - {item.listing_url}")
            lines.append(f"   - {item.rationale}")

    lines.extend(["", "## Potential Good Deals", ""])

    if not potential:
        lines.append("- No potential deals found.")
    else:
        for idx, item in enumerate(potential, start=1):
            lines.append(
                f"{idx}. {item.listing_title} | profit={item.expected_profit_jpy} JPY | "
                f"confidence={item.confidence:.2f} | proxy={item.recommended_proxy}"
            )
            lines.append(f"   - {item.listing_url}")
            lines.append(f"   - {item.rationale}")

    lines.append("")
    return "\n".join(lines)


def generate_daily_report(session: Session, report_date: date) -> DailyReportResponse:
    generated_at = datetime.now(timezone.utc)
    confident = list_ranked_listings(session, bucket="confident", limit=100)
    potential = list_ranked_listings(session, bucket="potential", limit=100)

    markdown = _render_markdown(report_date, generated_at, confident, potential)

    settings = get_settings()
    report_dir = _repo_root() / settings.reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{report_date.isoformat()}.md"
    report_path.write_text(markdown, encoding="utf-8")

    run = session.scalar(select(ReportRun).where(ReportRun.report_date == report_date))
    if run is None:
        run = ReportRun(report_date=report_date)

    run.generated_at = generated_at
    run.report_path = str(report_path.relative_to(_repo_root()))
    session.add(run)
    session.flush()

    session.execute(delete(ReportItem).where(ReportItem.report_run_id == run.report_run_id))

    for rank, item in enumerate(confident, start=1):
        session.add(
            ReportItem(
                report_run_id=run.report_run_id,
                listing_id=item.listing_id,
                bucket="confident",
                rank_position=rank,
            )
        )

    for rank, item in enumerate(potential, start=1):
        session.add(
            ReportItem(
                report_run_id=run.report_run_id,
                listing_id=item.listing_id,
                bucket="potential",
                rank_position=rank,
            )
        )

    session.commit()

    return DailyReportResponse(
        date=report_date,
        generated_at=generated_at,
        report_path=run.report_path,
        confident=confident,
        potential=potential,
    )


def get_daily_report(session: Session, report_date: date) -> DailyReportResponse | None:
    run = session.scalar(select(ReportRun).where(ReportRun.report_date == report_date))
    if run is None:
        return None

    confident_items = session.scalars(
        select(ReportItem)
        .where(ReportItem.report_run_id == run.report_run_id, ReportItem.bucket == "confident")
        .order_by(ReportItem.rank_position.asc())
    ).all()

    potential_items = session.scalars(
        select(ReportItem)
        .where(ReportItem.report_run_id == run.report_run_id, ReportItem.bucket == "potential")
        .order_by(ReportItem.rank_position.asc())
    ).all()

    confident: list[ListingSummary] = []
    potential: list[ListingSummary] = []

    for item in confident_items:
        summary = get_listing_summary(session, item.listing_id)
        if summary is not None:
            confident.append(summary)

    for item in potential_items:
        summary = get_listing_summary(session, item.listing_id)
        if summary is not None:
            potential.append(summary)

    return DailyReportResponse(
        date=run.report_date,
        generated_at=run.generated_at,
        report_path=run.report_path,
        confident=confident,
        potential=potential,
    )
