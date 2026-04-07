from ..models import RawListing


def predict_resale_value(
    listing: RawListing,
    classification_payload: dict,
) -> dict:
    multipliers = {
        "Pilot": 1.75,
        "Namiki": 2.0,
        "Sailor": 1.6,
        "Platinum": 1.5,
        "Nakaya": 1.9,
        "Pelikan": 1.7,
        "Montblanc": 1.85,
        "Unknown": 1.3,
    }

    base_price = listing.price_buy_now_jpy or listing.current_price_jpy or 12000
    brand = classification_payload["brand"]
    multiplier = multipliers.get(brand, 1.3)

    resale_pred = int(base_price * multiplier)
    item_count = classification_payload["item_count_estimate"]
    if item_count > 1:
        resale_pred = int(resale_pred * (1 + 0.68 * (item_count - 1)))

    grade_penalty = {
        "A": 1.0,
        "B+": 0.95,
        "B": 0.9,
        "C": 0.75,
        "Parts/Repair": 0.45,
    }
    resale_pred = int(resale_pred * grade_penalty.get(classification_payload["condition_grade"], 0.85))

    ci_margin = max(2000, int(resale_pred * 0.15))
    low = max(1000, resale_pred - ci_margin)
    high = resale_pred + ci_margin

    valuation_confidence = min(
        0.94,
        0.45
        + (0.15 if brand != "Unknown" else 0.0)
        + (classification_payload["classification_confidence"] * 0.32),
    )

    return {
        "resale_pred_jpy": resale_pred,
        "resale_ci_low_jpy": low,
        "resale_ci_high_jpy": high,
        "valuation_confidence": round(valuation_confidence, 3),
    }


def predict_auction_value(
    listing: RawListing,
    valuation_payload: dict,
) -> dict | None:
    if listing.listing_format != "auction":
        return None

    current_price = max(1, listing.current_price_jpy)
    bid_count = listing.bid_count or 0
    expected = int(current_price * (1.08 + min(0.25, bid_count * 0.03)))
    expected = max(expected, current_price + 500)
    expected = min(expected, int(valuation_payload["resale_pred_jpy"] * 0.92))

    low_win = max(int(current_price * 1.02), current_price + 200)
    confidence = min(0.91, 0.55 + min(0.25, bid_count * 0.04))

    return {
        "auction_low_win_price_jpy": int(low_win),
        "auction_expected_final_price_jpy": int(expected),
        "auction_confidence": round(confidence, 3),
    }
