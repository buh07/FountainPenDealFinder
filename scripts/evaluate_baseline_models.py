#!/usr/bin/env python3
"""Evaluate baseline resale/auction artifacts and enforce simple quality gates."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "labeled"
MODELS_DIR = REPO_ROOT / "models"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _mape(actual: float, predicted: float) -> float:
    return abs(actual - predicted) / max(1.0, abs(actual))


def evaluate_resale(rows: list[dict[str, str]], artifact: dict) -> dict:
    if not rows or not artifact:
        return {
            "rows": 0,
            "mape": None,
        }

    default_multiplier = float(artifact.get("default_multiplier", 1.3))
    condition_penalties = artifact.get("condition_penalties") or {}
    brand_multipliers = artifact.get("brand_multipliers") or {}
    lot_item_uplift = float(artifact.get("lot_item_uplift", 0.68))

    errors: list[float] = []
    used = 0
    for row in rows:
        ask = int(float(row.get("ask_price_jpy") or 0))
        sold = int(float(row.get("sold_price_jpy") or 0))
        if ask <= 0 or sold <= 0:
            continue

        brand = row.get("brand") or "Unknown"
        condition = row.get("condition_grade") or "B"
        item_count = max(1, int(float(row.get("item_count") or 1)))

        brand_mult = float(brand_multipliers.get(brand, default_multiplier))
        cond_penalty = float(condition_penalties.get(condition, artifact.get("default_condition_penalty", 0.85)))

        pred = ask * brand_mult * cond_penalty
        if item_count > 1:
            pred *= 1 + lot_item_uplift * (item_count - 1)

        errors.append(_mape(sold, pred))
        used += 1

    return {
        "rows": used,
        "mape": round(mean(errors), 4) if errors else None,
    }


def _bucket_for_bid_count(bid_count: int) -> str:
    if bid_count <= 0:
        return "0"
    if bid_count <= 3:
        return "1_3"
    if bid_count <= 7:
        return "4_7"
    return "8_plus"


def evaluate_auction(rows: list[dict[str, str]], artifact: dict) -> dict:
    if not rows or not artifact:
        return {
            "rows": 0,
            "mape": None,
        }

    expected_map = artifact.get("bid_bucket_expected_multipliers") or {}
    default_expected = float(artifact.get("default_expected_multiplier", 1.12))

    errors: list[float] = []
    used = 0
    for row in rows:
        current = int(float(row.get("current_price_jpy") or 0))
        final = int(float(row.get("final_price_jpy") or 0))
        bid_count = int(float(row.get("bid_count") or 0))
        if current <= 0 or final <= 0:
            continue

        bucket = _bucket_for_bid_count(bid_count)
        multiplier = float(expected_map.get(bucket, default_expected))
        pred = current * multiplier

        errors.append(_mape(final, pred))
        used += 1

    return {
        "rows": used,
        "mape": round(mean(errors), 4) if errors else None,
    }


def build_report(
    resale_eval: dict,
    auction_eval: dict,
    min_rows: int,
    resale_max_mape: float,
    auction_max_mape: float,
) -> dict:
    resale_gate = (
        resale_eval["rows"] >= min_rows
        and resale_eval["mape"] is not None
        and float(resale_eval["mape"]) <= resale_max_mape
    )
    auction_gate = (
        auction_eval["rows"] >= min_rows
        and auction_eval["mape"] is not None
        and float(auction_eval["mape"]) <= auction_max_mape
    )

    overall = resale_gate and auction_gate

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gates": {
            "overall_pass": overall,
            "resale_pass": resale_gate,
            "auction_pass": auction_gate,
            "min_rows": min_rows,
            "resale_max_mape": resale_max_mape,
            "auction_max_mape": auction_max_mape,
        },
        "metrics": {
            "resale": resale_eval,
            "auction": auction_eval,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate baseline model artifacts")
    parser.add_argument(
        "--min-rows",
        type=int,
        default=5,
        help="Minimum rows required per task",
    )
    parser.add_argument(
        "--resale-max-mape",
        type=float,
        default=0.5,
        help="Maximum allowed resale MAPE",
    )
    parser.add_argument(
        "--auction-max-mape",
        type=float,
        default=0.4,
        help="Maximum allowed auction MAPE",
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=str(MODELS_DIR / "eval" / "baseline_eval_v1.json"),
        help="Path to write evaluation report JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    resale_rows = _read_csv(DATA_DIR / "pen_swap_sales.csv")
    auction_rows = _read_csv(DATA_DIR / "yahoo_auction_outcomes.csv")

    resale_artifact = _read_json(MODELS_DIR / "resale" / "baseline_v1.json")
    auction_artifact = _read_json(MODELS_DIR / "yahoo-auction" / "baseline_v1.json")

    resale_eval = evaluate_resale(resale_rows, resale_artifact)
    auction_eval = evaluate_auction(auction_rows, auction_artifact)

    report = build_report(
        resale_eval,
        auction_eval,
        min_rows=max(1, args.min_rows),
        resale_max_mape=max(0.0, args.resale_max_mape),
        auction_max_mape=max(0.0, args.auction_max_mape),
    )

    report_path = Path(args.report_path)
    if not report_path.is_absolute():
        report_path = REPO_ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "PASS" if report["gates"]["overall_pass"] else "FAIL"
    print(
        "baseline evaluation "
        f"{status}: "
        f"resale_mape={report['metrics']['resale']['mape']} "
        f"auction_mape={report['metrics']['auction']['mape']}"
    )

    return 0 if report["gates"]["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
