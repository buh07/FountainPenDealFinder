#!/usr/bin/env python3
"""Build normalized training CSV datasets from raw historical JSONL sources."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "labeled" / "raw"
OUT_DIR = REPO_ROOT / "data" / "labeled"
API_ROOT = REPO_ROOT / "apps" / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.taxonomy import canonicalize_condition_grade, resolve_taxonomy  # noqa: E402


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)

    return rows


def _feedback_pricing_path() -> Path:
    value = os.environ.get(
        "FEEDBACK_PRICING_LABELS_PATH",
        "data/labeled/raw/pen_swap_sales_feedback.jsonl",
    )
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def build_pen_swap_dataset() -> int:
    raw_path = RAW_DIR / "pen_swap_sales.jsonl"
    feedback_path = _feedback_pricing_path()
    out_path = OUT_DIR / "pen_swap_sales.csv"
    rows = _load_jsonl(raw_path) + _load_jsonl(feedback_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source",
        "category",
        "classification_id",
        "brand",
        "line",
        "condition_grade",
        "ask_price_jpy",
        "sold_price_jpy",
        "item_count",
        "sold_at",
    ]

    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            taxonomy = resolve_taxonomy(
                brand=row.get("brand"),
                line=row.get("line"),
                classification_id=row.get("classification_id"),
                text=row.get("title"),
            )
            condition_grade = canonicalize_condition_grade(row.get("condition_grade"))
            writer.writerow(
                {
                    "source": row.get("source", "r_pen_swap"),
                    "category": taxonomy["category"] or "other",
                    "classification_id": taxonomy["classification_id"] or "unknown_fountain_pen",
                    "brand": taxonomy["brand"] or "Unknown",
                    "line": taxonomy["line"] or "fountain_pen",
                    "condition_grade": condition_grade,
                    "ask_price_jpy": int(row.get("ask_price_jpy") or 0),
                    "sold_price_jpy": int(row.get("sold_price_jpy") or 0),
                    "item_count": int(row.get("item_count") or 1),
                    "sold_at": row.get("sold_at", ""),
                }
            )

    return len(rows)


def build_yahoo_outcome_dataset() -> int:
    raw_path = RAW_DIR / "yahoo_auction_outcomes.jsonl"
    out_path = OUT_DIR / "yahoo_auction_outcomes.csv"
    rows = _load_jsonl(raw_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source",
        "category",
        "classification_id",
        "brand",
        "line",
        "current_price_jpy",
        "bid_count",
        "hours_to_end",
        "final_price_jpy",
        "ended_at",
    ]

    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            taxonomy = resolve_taxonomy(
                brand=row.get("brand"),
                line=row.get("line"),
                classification_id=row.get("classification_id"),
                text=row.get("title"),
            )
            writer.writerow(
                {
                    "source": row.get("source", "yahoo_auctions"),
                    "category": taxonomy["category"] or "other",
                    "classification_id": taxonomy["classification_id"] or "unknown_fountain_pen",
                    "brand": taxonomy["brand"] or "Unknown",
                    "line": taxonomy["line"] or "fountain_pen",
                    "current_price_jpy": int(row.get("current_price_jpy") or 0),
                    "bid_count": int(row.get("bid_count") or 0),
                    "hours_to_end": int(row.get("hours_to_end") or 0),
                    "final_price_jpy": int(row.get("final_price_jpy") or 0),
                    "ended_at": row.get("ended_at", ""),
                }
            )

    return len(rows)


def main() -> None:
    resale_rows = build_pen_swap_dataset()
    auction_rows = build_yahoo_outcome_dataset()
    print(
        "built historical datasets: "
        f"pen_swap_sales={resale_rows} rows, "
        f"yahoo_auction_outcomes={auction_rows} rows"
    )


if __name__ == "__main__":
    main()
