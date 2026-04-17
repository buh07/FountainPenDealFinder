#!/usr/bin/env python3
"""
Label downloaded pen images using Claude Vision API.

Reads data/images/manifest.jsonl, sends each un-labeled image to Claude,
writes results to data/images/labeled_manifest.jsonl.

Usage:
    python3 scripts/label_images_claude.py
    python3 scripts/label_images_claude.py --limit 200
    python3 scripts/label_images_claude.py --relabel   # re-label already processed images
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import anthropic

ROOT          = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "data" / "images" / "manifest.jsonl"
LABELED_PATH  = ROOT / "data" / "images" / "labeled_manifest.jsonl"

SYSTEM_PROMPT = """You are an expert fountain pen identifier. Given an image of a fountain pen (or pen accessories), identify:
1. brand (e.g., Pilot, Sailor, Platinum, Nakaya, Namiki, Pelikan, Montblanc, TWSBI, Lamy, Kaweco, Parker, Waterman, Cross, Visconti, Stipula, Edison, etc.)
2. line/model (e.g., Custom 742, Pro Gear, 3776 Century, Urushi, 1911, M800, 146, etc.)
3. condition (Mint/NOS, Excellent, Very Good, Good, Fair, Poor)
4. confidence (high/medium/low) — how certain are you about the identification?
5. notes — brief notes (finish, nib size if visible, color)

Respond ONLY with valid JSON, no markdown fences:
{"brand": "...", "line": "...", "condition": "...", "confidence": "...", "notes": "..."}

If the image doesn't show a fountain pen or is too unclear, respond:
{"brand": "Unknown", "line": "Unknown", "condition": "", "confidence": "low", "notes": "not a pen or unclear"}"""

USER_PROMPT = "Identify this fountain pen. Reply with JSON only."


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    rows = []
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def load_labeled_keys() -> set[str]:
    if not LABELED_PATH.exists():
        return set()
    keys = set()
    with LABELED_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    keys.add(json.loads(line)["image_path"])
                except Exception:
                    pass
    return keys


def append_labeled(entry: dict):
    LABELED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LABELED_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def image_to_base64(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception:
        return None


def label_image(client: anthropic.Anthropic, image_path: Path) -> dict | None:
    b64 = image_to_base64(image_path)
    if b64 is None:
        return None
    try:
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        # strip markdown fences if Claude added them anyway
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
    except anthropic.APIError as e:
        print(f"    [api error] {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Label pen images with Claude Vision")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max images to label this run (0 = all)")
    parser.add_argument("--relabel", action="store_true",
                        help="Re-label images already in labeled_manifest.jsonl")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    manifest = load_manifest()
    if not manifest:
        print(f"No entries in {MANIFEST_PATH}. Run collect_pen_images.py first.")
        sys.exit(1)

    labeled_keys = set() if args.relabel else load_labeled_keys()
    pending = [r for r in manifest if r["image_path"] not in labeled_keys]
    if args.limit:
        pending = pending[: args.limit]

    print(f"[label] Total manifest entries : {len(manifest)}")
    print(f"[label] Already labeled        : {len(labeled_keys)}")
    print(f"[label] To label this run      : {len(pending)}")

    if not pending:
        print("Nothing to label.")
        return

    client = anthropic.Anthropic(api_key=api_key)
    labeled = 0
    skipped = 0

    for i, entry in enumerate(pending, 1):
        img_path = ROOT / entry["image_path"]
        if not img_path.exists():
            print(f"  [{i}/{len(pending)}] MISSING  {entry['image_path']}")
            skipped += 1
            continue

        print(f"  [{i}/{len(pending)}] labeling {entry['image_path']}", end="", flush=True)
        result = label_image(client, img_path)

        if result is None:
            print(" → FAILED")
            skipped += 1
            continue

        labeled_entry = {
            **entry,
            "claude_brand":      result.get("brand", "Unknown"),
            "claude_line":       result.get("line", "Unknown"),
            "claude_condition":  result.get("condition", ""),
            "claude_confidence": result.get("confidence", "low"),
            "claude_notes":      result.get("notes", ""),
        }
        # If source manifest had low-confidence brand but Claude is high-confidence, upgrade
        if result.get("confidence") in ("high", "medium") and result.get("brand", "Unknown") != "Unknown":
            labeled_entry["brand"] = result["brand"]
            labeled_entry["line"]  = result.get("line", entry.get("line", "Unknown"))

        append_labeled(labeled_entry)
        labeled += 1
        print(f" → {result.get('brand','?')} {result.get('line','?')} [{result.get('confidence','?')}]")

        # ~3 req/s is well within Anthropic limits; add a small delay to be polite
        time.sleep(0.4)

    print(f"\n[done] Labeled {labeled} images  ({skipped} skipped)")
    print(f"       Output: {LABELED_PATH}")


if __name__ == "__main__":
    main()
