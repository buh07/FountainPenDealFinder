from datetime import datetime

from fastapi import APIRouter

from ..schemas import ListingSummary

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily/{date}")
def get_daily_report(date: str) -> dict:
    sample = ListingSummary(
        listing_id="sample-1",
        marketplace="yahoo_auctions",
        listing_title="Pilot Custom 743 14K M",
        listing_url="https://example.com/listing/sample-1",
        listing_type="auction",
        current_price_jpy=42000,
        estimated_total_buy_cost_jpy=51500,
        estimated_resale_price_jpy=86000,
        expected_profit_jpy=34500,
        expected_profit_pct=0.669,
        confidence=0.79,
        deal_bucket="confident",
        listed_at=datetime.utcnow(),
        time_remaining="3h 20m",
        rationale="Strong comparable sales and clean condition signals.",
    )
    return {
        "date": date,
        "generated_at": datetime.utcnow().isoformat(),
        "confident": [sample.model_dump()],
        "potential": [],
    }
