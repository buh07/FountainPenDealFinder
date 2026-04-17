#!/usr/bin/env python3
"""
Import reviewed sale rows from the CSV into the training JSONL files.

After reviewing data/labeled/review/scraped_sales_YYYYMMDD.csv:
  - Rows with review_status = "keep"  → imported as-is
  - Rows with review_status = "fix"   → override_* columns used
  - Rows with review_status = "discard" or blank → skipped

Yahoo rows  → data/labeled/raw/yahoo_auction_outcomes.jsonl
Pen_Swap rows → data/labeled/raw/pen_swap_sales.jsonl

Usage:
    python3 scripts/import_reviewed_sales.py --input data/labeled/review/scraped_sales_20260416_1200.csv
    python3 scripts/import_reviewed_sales.py --input data/labeled/review/scraped_sales_20260416_1200.csv --dry-run
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PEN_SWAP_JSONL = ROOT / "data" / "labeled" / "raw" / "pen_swap_sales.jsonl"
YAHOO_JSONL = ROOT / "data" / "labeled" / "raw" / "yahoo_auction_outcomes.jsonl"


def _apply_overrides(row: dict) -> dict:
    """Apply override_* columns to extracted_* values."""
    out = dict(row)
    if row.get("override_brand", "").strip():
        out["extracted_brand"] = row["override_brand"].strip()
    if row.get("override_line", "").strip():
        out["extracted_line"] = row["override_line"].strip()
    if row.get("override_condition", "").strip():
        out["extracted_condition"] = row["override_condition"].strip()
    if row.get("override_price_jpy", "").strip():
        try:
            out["price_jpy"] = int(row["override_price_jpy"].strip().replace(",", ""))
        except ValueError:
            pass
    return out


def _to_pen_swap_record(row: dict) -> dict:
    """Convert a reviewed CSV row to pen_swap_sales JSONL record."""
    try:
        ask_price_jpy = int(str(row.get("price_jpy") or 0).replace(",", "")) or None
    except ValueError:
        ask_price_jpy = None

    try:
        final = str(row.get("final_price_jpy") or "").replace(",", "")
        sold_price_jpy = int(final) if final else ask_price_jpy
    except ValueError:
        sold_price_jpy = ask_price_jpy

    try:
        item_count = int(str(row.get("item_count") or 1))
    except ValueError:
        item_count = 1

    return {
        "source": "r_pen_swap",
        "brand": row.get("extracted_brand") or "Unknown",
        "line": row.get("extracted_line") or "Unknown",
        "condition_grade": row.get("extracted_condition") or "B",
        "ask_price_jpy": ask_price_jpy,
        "sold_price_jpy": sold_price_jpy,
        "item_count": item_count,
        "sold_at": row.get("ended_at") or datetime.now(timezone.utc).isoformat(),
        "_source_url": row.get("source_url", ""),
        "_raw_title": row.get("raw_title", ""),
        "_notes": row.get("notes", ""),
    }


def _to_yahoo_record(row: dict) -> dict:
    """Convert a reviewed CSV row to yahoo_auction_outcomes JSONL record."""
    try:
        current_price_jpy = int(str(row.get("price_jpy") or 0).replace(",", "")) or None
    except ValueError:
        current_price_jpy = None

    try:
        final = str(row.get("final_price_jpy") or "").replace(",", "")
        final_price_jpy = int(final) if final else current_price_jpy
    except ValueError:
        final_price_jpy = current_price_jpy

    try:
        bid_count = int(str(row.get("bid_count") or 0))
    except ValueError:
        bid_count = 0

    return {
        "source": "yahoo_auctions",
        "brand": row.get("extracted_brand") or "Unknown",
        "line": row.get("extracted_line") or "Unknown",
        "current_price_jpy": current_price_jpy,
        "bid_count": bid_count,
        "hours_to_end": 0,
        "final_price_jpy": final_price_jpy,
        "ended_at": row.get("ended_at") or datetime.now(timezone.utc).isoformat(),
        "_source_url": row.get("source_url", ""),
        "_raw_title": row.get("raw_title", ""),
        "_notes": row.get("notes", ""),
    }


def _existing_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _dedup_key_pen_swap(record: dict) -> str:
    return f"{record.get('brand')}|{record.get('line')}|{record.get('condition_grade')}|{record.get('sold_at', '')[:10]}"


def _dedup_key_yahoo(record: dict) -> str:
    url = record.get("_source_url", "")
    return url if url else f"{record.get('brand')}|{record.get('line')}|{record.get('ended_at', '')[:10]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import reviewed sales CSV into training JSONL")
    parser.add_argument("--input", required=True, help="Path to the reviewed CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be imported without writing")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    pen_swap_new: list[dict] = []
    yahoo_new: list[dict] = []
    skipped = 0
    errors = 0

    with input_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            status = row.get("review_status", "").strip().lower()
            if status not in ("keep", "fix"):
                skipped += 1
                continue

            row = _apply_overrides(row)

            # Validate minimum required fields
            brand = row.get("extracted_brand", "").strip()
            price_jpy = row.get("price_jpy", "").strip()
            if not brand or not price_jpy:
                print(f"  [skip row {i}] missing brand or price — mark as discard or fill overrides")
                errors += 1
                continue

            source = row.get("source", "")
            if source == "r_pen_swap":
                pen_swap_new.append(_to_pen_swap_record(row))
            elif source == "yahoo_auctions":
                yahoo_new.append(_to_yahoo_record(row))
            else:
                print(f"  [skip row {i}] unknown source: {source!r}")
                errors += 1

    # Dedup against existing records
    existing_ps = _existing_records(PEN_SWAP_JSONL)
    existing_yahoo = _existing_records(YAHOO_JSONL)
    existing_ps_keys = {_dedup_key_pen_swap(r) for r in existing_ps}
    existing_yahoo_keys = {_dedup_key_yahoo(r) for r in existing_yahoo}

    ps_to_write = [r for r in pen_swap_new if _dedup_key_pen_swap(r) not in existing_ps_keys]
    yahoo_to_write = [r for r in yahoo_new if _dedup_key_yahoo(r) not in existing_yahoo_keys]

    ps_dupes = len(pen_swap_new) - len(ps_to_write)
    yahoo_dupes = len(yahoo_new) - len(yahoo_to_write)

    print(f"\nImport summary:")
    print(f"  Pen_Swap: {len(ps_to_write)} new rows ({ps_dupes} duplicates skipped)")
    print(f"  Yahoo:    {len(yahoo_to_write)} new rows ({yahoo_dupes} duplicates skipped)")
    print(f"  Skipped (not keep/fix): {skipped}")
    print(f"  Errors (missing fields): {errors}")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        if ps_to_write:
            print("\nSample Pen_Swap records:")
            for r in ps_to_write[:3]:
                print(" ", json.dumps(r, ensure_ascii=False))
        if yahoo_to_write:
            print("\nSample Yahoo records:")
            for r in yahoo_to_write[:3]:
                print(" ", json.dumps(r, ensure_ascii=False))
        return

    if not ps_to_write and not yahoo_to_write:
        print("\nNothing to import.")
        return

    confirm = input(f"\nWrite {len(ps_to_write)} Pen_Swap + {len(yahoo_to_write)} Yahoo rows? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    if ps_to_write:
        with PEN_SWAP_JSONL.open("a", encoding="utf-8") as f:
            for record in ps_to_write:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  Appended {len(ps_to_write)} rows → {PEN_SWAP_JSONL}")

    if yahoo_to_write:
        with YAHOO_JSONL.open("a", encoding="utf-8") as f:
            for record in yahoo_to_write:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  Appended {len(yahoo_to_write)} rows → {YAHOO_JSONL}")

    print("\nDone. Run scripts/train_baseline_models.py to retrain with the new data.")


if __name__ == "__main__":
    main()
