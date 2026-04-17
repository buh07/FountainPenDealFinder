#!/usr/bin/env python3
"""
Merge manually reviewed data from data/labeled/reviewed/checked_sales.jsonl
into the training JSONL files used by the model pipeline.

Yahoo rows  → data/labeled/raw/yahoo_auction_outcomes.jsonl
Pen_Swap rows → data/labeled/raw/pen_swap_sales.jsonl

Usage:
    python3 scripts/merge_checked_to_training.py
    python3 scripts/merge_checked_to_training.py --dry-run
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_JSONL    = ROOT / "data" / "labeled" / "reviewed" / "checked_sales.jsonl"
PEN_SWAP_JSONL   = ROOT / "data" / "labeled" / "raw" / "pen_swap_sales.jsonl"
YAHOO_JSONL      = ROOT / "data" / "labeled" / "raw" / "yahoo_auction_outcomes.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
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


def _dedup_key_yahoo(r: dict) -> str:
    url = r.get("_source_url", "")
    return url if url else f"{r.get('brand')}|{r.get('line')}|{r.get('ended_at','')[:10]}"


def _dedup_key_penswap(r: dict) -> str:
    url = r.get("_source_url", "")
    return url if url else f"{r.get('brand')}|{r.get('line')}|{r.get('condition_grade')}|{r.get('sold_at','')[:10]}"


def main():
    parser = argparse.ArgumentParser(description="Merge reviewed checked_sales.jsonl into training data")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be merged without writing")
    args = parser.parse_args()

    if not CHECKED_JSONL.exists():
        print(f"No checked data found at {CHECKED_JSONL}")
        print("Run scripts/review_scraped_data.py first and keep some rows.")
        sys.exit(1)

    checked = _load_jsonl(CHECKED_JSONL)
    if not checked:
        print("checked_sales.jsonl is empty. Nothing to merge.")
        sys.exit(0)

    yahoo_checked  = [r for r in checked if r.get("source") == "yahoo_auctions"]
    penswap_checked = [r for r in checked if r.get("source") == "r_pen_swap"]

    existing_yahoo   = _load_jsonl(YAHOO_JSONL)
    existing_penswap = _load_jsonl(PEN_SWAP_JSONL)

    existing_yahoo_keys   = {_dedup_key_yahoo(r)    for r in existing_yahoo}
    existing_penswap_keys = {_dedup_key_penswap(r)  for r in existing_penswap}

    yahoo_new   = [r for r in yahoo_checked   if _dedup_key_yahoo(r)    not in existing_yahoo_keys]
    penswap_new = [r for r in penswap_checked if _dedup_key_penswap(r)  not in existing_penswap_keys]

    print(f"\nMerge summary")
    print(f"  checked_sales.jsonl:  {len(checked)} total rows")
    print(f"  Yahoo rows:           {len(yahoo_checked)} checked  →  {len(yahoo_new)} new (skipping {len(yahoo_checked)-len(yahoo_new)} duplicates)")
    print(f"  Pen_Swap rows:        {len(penswap_checked)} checked  →  {len(penswap_new)} new (skipping {len(penswap_checked)-len(penswap_new)} duplicates)")

    if args.dry_run:
        print("\n[dry-run] No files written.\n")
        if yahoo_new:
            print("Sample Yahoo records that would be added:")
            for r in yahoo_new[:3]:
                print(" ", json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False))
        if penswap_new:
            print("Sample Pen_Swap records that would be added:")
            for r in penswap_new[:3]:
                print(" ", json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False))
        return

    if not yahoo_new and not penswap_new:
        print("\nNothing new to add.")
        return

    confirm = input(f"\nWrite {len(yahoo_new)} Yahoo + {len(penswap_new)} Pen_Swap rows to training files? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    if yahoo_new:
        with YAHOO_JSONL.open("a", encoding="utf-8") as f:
            for r in yahoo_new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  Appended {len(yahoo_new)} rows → {YAHOO_JSONL}")

    if penswap_new:
        with PEN_SWAP_JSONL.open("a", encoding="utf-8") as f:
            for r in penswap_new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  Appended {len(penswap_new)} rows → {PEN_SWAP_JSONL}")

    print("\nDone. Next step: python3 scripts/train_baseline_models.py")


if __name__ == "__main__":
    main()
