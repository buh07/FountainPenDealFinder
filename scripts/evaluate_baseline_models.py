#!/usr/bin/env python3
"""Evaluate baseline resale/auction artifacts and enforce simple quality gates."""

from __future__ import annotations

import argparse
import csv
import hashlib
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


def _row_split(row: dict[str, str], train_ratio: float) -> str:
    normalized_ratio = min(0.95, max(0.05, float(train_ratio)))
    fingerprint = "|".join(f"{key}={row.get(key, '')}" for key in sorted(row.keys()))
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "train" if bucket < normalized_ratio else "test"


def _split_rows(rows: list[dict[str, str]], train_ratio: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    train_rows: list[dict[str, str]] = []
    test_rows: list[dict[str, str]] = []
    for row in rows:
        if _row_split(row, train_ratio) == "train":
            train_rows.append(row)
        else:
            test_rows.append(row)
    return train_rows, test_rows


def _error_metrics(apes: list[float], abs_errors: list[float], actuals: list[float]) -> dict:
    if not apes:
        return {"mape": None, "wape": None, "p95_ape": None}

    ordered = sorted(apes)
    p95_idx = max(0, int(len(ordered) * 0.95) - 1)
    total_actual = sum(abs(value) for value in actuals)
    wape = None
    if total_actual > 0:
        wape = sum(abs_errors) / total_actual
    return {
        "mape": round(mean(apes), 4),
        "wape": (round(wape, 4) if wape is not None else None),
        "p95_ape": round(ordered[p95_idx], 4),
    }


def evaluate_resale(rows: list[dict[str, str]], artifact: dict) -> dict:
    if not rows or not artifact:
        return {
            "rows": 0,
            "mape": None,
            "wape": None,
            "p95_ape": None,
            "by_brand": {},
        }

    default_multiplier = float(artifact.get("default_multiplier", 1.3))
    condition_penalties = artifact.get("condition_penalties") or {}
    brand_multipliers = artifact.get("brand_multipliers") or {}
    line_multipliers = artifact.get("line_multipliers") or {}
    lot_item_uplift = float(artifact.get("lot_item_uplift", 0.68))

    errors: list[float] = []
    abs_errors: list[float] = []
    actuals: list[float] = []
    used = 0
    per_brand: dict[str, list[float]] = {}
    for row in rows:
        ask = int(float(row.get("ask_price_jpy") or 0))
        sold = int(float(row.get("sold_price_jpy") or 0))
        if ask <= 0 or sold <= 0:
            continue

        brand = row.get("brand") or "Unknown"
        classification_id = row.get("classification_id") or "unknown_fountain_pen"
        condition = row.get("condition_grade") or "B"
        item_count = max(1, int(float(row.get("item_count") or 1)))

        line_mult = line_multipliers.get(classification_id)
        brand_mult = float(line_mult) if line_mult is not None else float(brand_multipliers.get(brand, default_multiplier))
        cond_penalty = float(condition_penalties.get(condition, artifact.get("default_condition_penalty", 0.85)))

        pred = ask * brand_mult * cond_penalty
        if item_count > 1:
            pred *= 1 + lot_item_uplift * (item_count - 1)

        ape = _mape(sold, pred)
        abs_err = abs(float(sold) - float(pred))
        errors.append(ape)
        abs_errors.append(abs_err)
        actuals.append(float(sold))
        per_brand.setdefault(brand, []).append(ape)
        used += 1

    metrics = _error_metrics(errors, abs_errors, actuals)
    brand_metrics = {
        brand: {
            "rows": len(values),
            "mape": round(mean(values), 4) if values else None,
        }
        for brand, values in sorted(per_brand.items())
    }
    return {
        "rows": used,
        "mape": metrics["mape"],
        "wape": metrics["wape"],
        "p95_ape": metrics["p95_ape"],
        "by_brand": brand_metrics,
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
            "wape": None,
            "p95_ape": None,
            "by_bid_bucket": {},
        }

    expected_map = artifact.get("bid_bucket_expected_multipliers") or {}
    default_expected = float(artifact.get("default_expected_multiplier", 1.12))

    errors: list[float] = []
    abs_errors: list[float] = []
    actuals: list[float] = []
    used = 0
    per_bucket: dict[str, list[float]] = {}
    for row in rows:
        current = int(float(row.get("current_price_jpy") or 0))
        final = int(float(row.get("final_price_jpy") or 0))
        bid_count = int(float(row.get("bid_count") or 0))
        if current <= 0 or final <= 0:
            continue

        bucket = _bucket_for_bid_count(bid_count)
        multiplier = float(expected_map.get(bucket, default_expected))
        pred = current * multiplier

        ape = _mape(final, pred)
        abs_err = abs(float(final) - float(pred))
        errors.append(ape)
        abs_errors.append(abs_err)
        actuals.append(float(final))
        per_bucket.setdefault(bucket, []).append(ape)
        used += 1

    metrics = _error_metrics(errors, abs_errors, actuals)
    bucket_metrics = {
        bucket: {
            "rows": len(values),
            "mape": round(mean(values), 4) if values else None,
        }
        for bucket, values in sorted(per_bucket.items())
    }
    return {
        "rows": used,
        "mape": metrics["mape"],
        "wape": metrics["wape"],
        "p95_ape": metrics["p95_ape"],
        "by_bid_bucket": bucket_metrics,
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
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Deterministic hash split train ratio in (0,1); evaluation runs on holdout test split",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    resale_rows = _read_csv(DATA_DIR / "pen_swap_sales.csv")
    auction_rows = _read_csv(DATA_DIR / "yahoo_auction_outcomes.csv")
    train_ratio = min(0.95, max(0.05, float(args.train_ratio)))
    _resale_train, resale_test = _split_rows(resale_rows, train_ratio)
    _auction_train, auction_test = _split_rows(auction_rows, train_ratio)
    min_rows = max(1, args.min_rows)

    resale_eval_rows = resale_test
    resale_eval_mode = "holdout_test"
    if len(resale_eval_rows) < min_rows:
        resale_eval_rows = resale_rows
        resale_eval_mode = "all_rows_fallback_small_holdout"

    auction_eval_rows = auction_test
    auction_eval_mode = "holdout_test"
    if len(auction_eval_rows) < min_rows:
        auction_eval_rows = auction_rows
        auction_eval_mode = "all_rows_fallback_small_holdout"

    resale_artifact = _read_json(MODELS_DIR / "resale" / "baseline_v1.json")
    auction_artifact = _read_json(MODELS_DIR / "yahoo-auction" / "baseline_v1.json")

    resale_eval = evaluate_resale(resale_eval_rows, resale_artifact)
    auction_eval = evaluate_auction(auction_eval_rows, auction_artifact)

    report = build_report(
        resale_eval,
        auction_eval,
        min_rows=min_rows,
        resale_max_mape=max(0.0, args.resale_max_mape),
        auction_max_mape=max(0.0, args.auction_max_mape),
    )
    report["split"] = {
        "strategy": "deterministic_hash",
        "train_ratio": train_ratio,
        "resale_test_rows": len(resale_test),
        "auction_test_rows": len(auction_test),
        "resale_eval_mode": resale_eval_mode,
        "auction_eval_mode": auction_eval_mode,
        "resale_evaluated_rows": len(resale_eval_rows),
        "auction_evaluated_rows": len(auction_eval_rows),
    }

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
