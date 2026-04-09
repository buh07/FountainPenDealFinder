import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
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
from .listing_quality import derive_price_status, get_default_timezone, local_day_bounds_utc, to_utc


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
    target = to_utc(ends_at)
    if target is None:
        return None

    delta = target - now
    if delta.total_seconds() <= 0:
        return "ended"

    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _listing_summary_from_related(
    listing: RawListing,
    classification: ClassificationResult | None,
    valuation: ValuationPrediction | None,
    auction: AuctionPrediction | None,
    deal: DealScore | None,
    proxy: ProxyOptionEstimate | None,
) -> ListingSummary:
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

    risk_flags = _from_json(deal.risk_flags_json if deal else None, [])
    if not isinstance(risk_flags, list):
        risk_flags = []

    default_total_cost = (
        (listing.price_buy_now_jpy or listing.current_price_jpy)
        + (listing.domestic_shipping_jpy or 0)
    )

    price_status = derive_price_status(
        listing.current_price_jpy,
        listing.price_buy_now_jpy,
        listing.raw_attributes_json,
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
        price_status=price_status,
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
        risk_flags=[str(flag) for flag in risk_flags],
        listed_at=listing.listed_at,
        time_remaining=_time_remaining(listing.ends_at),
        rationale=(deal.rationale if deal else "Not scored yet."),
    )


def _build_listing_summaries(
    session: Session,
    listings: list[RawListing],
) -> list[ListingSummary]:
    if not listings:
        return []

    listing_ids = [listing.listing_id for listing in listings]
    classification_rows = session.scalars(
        select(ClassificationResult).where(ClassificationResult.listing_id.in_(listing_ids))
    ).all()
    valuation_rows = session.scalars(
        select(ValuationPrediction).where(ValuationPrediction.listing_id.in_(listing_ids))
    ).all()
    auction_rows = session.scalars(
        select(AuctionPrediction).where(AuctionPrediction.listing_id.in_(listing_ids))
    ).all()
    deal_rows = session.scalars(
        select(DealScore).where(DealScore.listing_id.in_(listing_ids))
    ).all()
    proxy_rows = session.scalars(
        select(ProxyOptionEstimate).where(
            ProxyOptionEstimate.listing_id.in_(listing_ids),
            ProxyOptionEstimate.is_recommended.is_(True),
        )
    ).all()

    classifications = {row.listing_id: row for row in classification_rows}
    valuations = {row.listing_id: row for row in valuation_rows}
    auctions = {row.listing_id: row for row in auction_rows}
    deals = {row.listing_id: row for row in deal_rows}
    proxies = {row.listing_id: row for row in proxy_rows}

    return [
        _listing_summary_from_related(
            listing,
            classification=classifications.get(listing.listing_id),
            valuation=valuations.get(listing.listing_id),
            auction=auctions.get(listing.listing_id),
            deal=deals.get(listing.listing_id),
            proxy=proxies.get(listing.listing_id),
        )
        for listing in listings
    ]


def _summary_map_for_listing_ids(session: Session, listing_ids: list[str]) -> dict[str, ListingSummary]:
    if not listing_ids:
        return {}

    listings = session.scalars(select(RawListing).where(RawListing.listing_id.in_(listing_ids))).all()
    summaries = _build_listing_summaries(session, listings)
    return {summary.listing_id: summary for summary in summaries}


def get_listing_summary(session: Session, listing_id: str) -> ListingSummary | None:
    listing = session.scalar(select(RawListing).where(RawListing.listing_id == listing_id))
    if listing is None:
        return None
    return _build_listing_summaries(session, [listing])[0]


def _is_in_report_window(
    listing: RawListing,
    report_date: date,
    generated_at: datetime,
) -> bool:
    settings = get_settings()
    default_tz = get_default_timezone(settings.default_timezone)

    if listing.listing_format == "auction":
        ends_at = to_utc(listing.ends_at)
        if ends_at is None:
            return False
        return generated_at <= ends_at < (generated_at + timedelta(hours=24))

    listed_at = to_utc(listing.listed_at)
    if listed_at is None:
        return False

    day_start_utc, day_end_utc = local_day_bounds_utc(report_date, default_tz)
    return day_start_utc <= listed_at < day_end_utc


def list_ranked_listings(
    session: Session,
    source: str | None = None,
    bucket: str | None = None,
    limit: int = 50,
    offset: int = 0,
    report_date: date | None = None,
    generated_at: datetime | None = None,
) -> list[ListingSummary]:
    query_limit = limit + max(0, offset)
    if report_date is not None and generated_at is not None:
        query_limit = max((limit + max(0, offset)) * 5, 100)

    stmt = (
        select(RawListing)
        .join(DealScore, DealScore.listing_id == RawListing.listing_id)
        .order_by(DealScore.risk_adjusted_profit_jpy.desc())
        .limit(query_limit)
    )

    if source:
        stmt = stmt.where(RawListing.source == source)

    if bucket:
        stmt = stmt.where(DealScore.bucket == bucket)
    else:
        stmt = stmt.where(DealScore.bucket.in_(["confident", "potential"]))

    listings = session.scalars(stmt).all()

    filtered: list[RawListing] = []
    for listing in listings:
        if report_date is not None and generated_at is not None:
            if not _is_in_report_window(listing, report_date=report_date, generated_at=generated_at):
                continue
        filtered.append(listing)
        if len(filtered) >= (offset + limit):
            break

    paged = filtered[offset : offset + limit]
    results = _build_listing_summaries(session, paged)
    return results


def count_ranked_listings(
    session: Session,
    source: str | None = None,
    bucket: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(RawListing).join(
        DealScore,
        DealScore.listing_id == RawListing.listing_id,
    )
    if source:
        stmt = stmt.where(RawListing.source == source)
    if bucket:
        stmt = stmt.where(DealScore.bucket == bucket)
    else:
        stmt = stmt.where(DealScore.bucket.in_(["confident", "potential"]))
    return int(session.scalar(stmt) or 0)


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
            if item.price_status != "valid":
                lines.append(
                    f"   - data_quality: price_status={item.price_status}; "
                    f"risk_flags={', '.join(item.risk_flags) if item.risk_flags else 'none'}"
                )

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
            if item.price_status != "valid":
                lines.append(
                    f"   - data_quality: price_status={item.price_status}; "
                    f"risk_flags={', '.join(item.risk_flags) if item.risk_flags else 'none'}"
                )

    lines.append("")
    return "\n".join(lines)


def generate_daily_report(session: Session, report_date: date) -> DailyReportResponse:
    generated_at = datetime.now(timezone.utc)
    confident = list_ranked_listings(
        session,
        bucket="confident",
        limit=100,
        report_date=report_date,
        generated_at=generated_at,
    )
    potential = list_ranked_listings(
        session,
        bucket="potential",
        limit=100,
        report_date=report_date,
        generated_at=generated_at,
    )

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

    ordered_ids = [item.listing_id for item in confident_items] + [item.listing_id for item in potential_items]
    summary_map = _summary_map_for_listing_ids(session, ordered_ids)
    confident = [summary_map[item.listing_id] for item in confident_items if item.listing_id in summary_map]
    potential = [summary_map[item.listing_id] for item in potential_items if item.listing_id in summary_map]

    return DailyReportResponse(
        date=run.report_date,
        generated_at=run.generated_at,
        report_path=run.report_path,
        confident=confident,
        potential=potential,
    )
