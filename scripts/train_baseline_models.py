#!/usr/bin/env python3
"""Train lightweight baseline resale and auction models from historical CSV datasets."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "labeled"
MODEL_DIR_RESALE = REPO_ROOT / "models" / "resale"
MODEL_DIR_AUCTION = REPO_ROOT / "models" / "yahoo-auction"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [dict(row) for row in reader]


def _p10(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, int(len(ordered) * 0.1) - 1)
    return ordered[idx]


def train_resale_model(rows: list[dict[str, str]]) -> dict:
    clean_rows: list[dict] = []
    for row in rows:
        ask = int(float(row.get("ask_price_jpy") or 0))
        sold = int(float(row.get("sold_price_jpy") or 0))
        if ask <= 0 or sold <= 0:
            continue
        clean_rows.append(
            {
                "brand": row.get("brand") or "Unknown",
                "condition_grade": row.get("condition_grade") or "B",
                "item_count": max(1, int(float(row.get("item_count") or 1))),
                "ask_price_jpy": ask,
                "sold_price_jpy": sold,
                "ratio": sold / ask,
            }
        )

    if not clean_rows:
        return {}

    brand_ratios: dict[str, list[float]] = defaultdict(list)
    condition_ratios: dict[str, list[float]] = defaultdict(list)
    lot_uplifts: list[float] = []

    for row in clean_rows:
        brand_ratios[row["brand"]].append(row["ratio"])
        condition_ratios[row["condition_grade"]].append(row["ratio"])

        if row["item_count"] > 1:
            uplift = max(0.0, (row["ratio"] - 1.0) / (row["item_count"] - 1))
            lot_uplifts.append(uplift)

    default_multiplier = median([row["ratio"] for row in clean_rows])

    brand_multipliers = {
        brand: round(median(values), 4)
        for brand, values in sorted(brand_ratios.items())
    }

    condition_penalties = {}
    for condition, values in sorted(condition_ratios.items()):
        cond_ratio = median(values)
        penalty = cond_ratio / max(default_multiplier, 1e-6)
        condition_penalties[condition] = round(min(1.15, max(0.35, penalty)), 4)

    lot_item_uplift = median(lot_uplifts) if lot_uplifts else 0.68

    rel_errors: list[float] = []
    for row in clean_rows:
        brand_mult = brand_multipliers.get(row["brand"], default_multiplier)
        cond_penalty = condition_penalties.get(row["condition_grade"], 1.0)
        pred = row["ask_price_jpy"] * brand_mult * cond_penalty
        if row["item_count"] > 1:
            pred *= (1 + lot_item_uplift * (row["item_count"] - 1))
        rel_errors.append(abs(row["sold_price_jpy"] - pred) / max(1, row["sold_price_jpy"]))

    ci_pct = min(0.4, max(0.1, median(rel_errors) * 1.5))

    return {
        "artifact": "resale_baseline",
        "version": "v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows_used": len(clean_rows),
        "default_multiplier": round(default_multiplier, 4),
        "brand_multipliers": brand_multipliers,
        "condition_penalties": condition_penalties,
        "default_condition_penalty": 0.85,
        "lot_item_uplift": round(float(lot_item_uplift), 4),
        "ci_pct": round(float(ci_pct), 4),
        "confidence_base": 0.57,
    }


def _bucket_for_bid_count(bid_count: int) -> str:
    if bid_count <= 0:
        return "0"
    if bid_count <= 3:
        return "1_3"
    if bid_count <= 7:
        return "4_7"
    return "8_plus"


def train_auction_model(rows: list[dict[str, str]]) -> dict:
    clean_rows: list[dict] = []
    for row in rows:
        current = int(float(row.get("current_price_jpy") or 0))
        final = int(float(row.get("final_price_jpy") or 0))
        bid_count = int(float(row.get("bid_count") or 0))
        if current <= 0 or final <= 0:
            continue
        clean_rows.append(
            {
                "current_price_jpy": current,
                "final_price_jpy": final,
                "bid_count": bid_count,
                "ratio": final / current,
            }
        )

    if not clean_rows:
        return {}

    bucket_ratios: dict[str, list[float]] = defaultdict(list)
    for row in clean_rows:
        bucket_ratios[_bucket_for_bid_count(row["bid_count"])].append(row["ratio"])

    expected_multipliers = {
        bucket: round(median(values), 4)
        for bucket, values in sorted(bucket_ratios.items())
    }
    low_multipliers = {
        bucket: round(_p10(values), 4)
        for bucket, values in sorted(bucket_ratios.items())
    }

    overall = [row["ratio"] for row in clean_rows]
    return {
        "artifact": "auction_baseline",
        "version": "v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "rows_used": len(clean_rows),
        "bid_bucket_expected_multipliers": expected_multipliers,
        "bid_bucket_low_multipliers": low_multipliers,
        "default_expected_multiplier": round(median(overall), 4),
        "default_low_multiplier": round(_p10(overall), 4),
        "max_resale_ratio": 0.92,
        "confidence_base": 0.6,
    }


def main() -> None:
    resale_rows = _read_csv(DATA_DIR / "pen_swap_sales.csv")
    auction_rows = _read_csv(DATA_DIR / "yahoo_auction_outcomes.csv")

    resale_artifact = train_resale_model(resale_rows)
    auction_artifact = train_auction_model(auction_rows)

    MODEL_DIR_RESALE.mkdir(parents=True, exist_ok=True)
    MODEL_DIR_AUCTION.mkdir(parents=True, exist_ok=True)

    if resale_artifact:
        (MODEL_DIR_RESALE / "baseline_v1.json").write_text(
            json.dumps(resale_artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if auction_artifact:
        (MODEL_DIR_AUCTION / "baseline_v1.json").write_text(
            json.dumps(auction_artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(
        "trained baseline models: "
        f"resale_rows={len(resale_rows)} "
        f"auction_rows={len(auction_rows)}"
    )


if __name__ == "__main__":
    main()
