#!/usr/bin/env python3
"""
Download pen images from r/Pen_Swap posts and save them as a labeled dataset.

Each image is saved to:
    data/images/raw/<brand>/<line_slug>/<post_id>_<n>.jpg

A manifest is written to:
    data/images/manifest.jsonl

Each manifest entry: {image_path, brand, line, condition, source_url,
                      post_id, post_title, label_confidence, downloaded_at}

Usage:
    python3 scripts/collect_pen_images.py
    python3 scripts/collect_pen_images.py --pages 20
    python3 scripts/collect_pen_images.py --min-confidence medium
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image
import io

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "data" / "taxonomy" / "taxonomy_v1_seed.csv"
IMAGES_DIR    = ROOT / "data" / "images" / "raw"
MANIFEST_PATH = ROOT / "data" / "images" / "manifest.jsonl"

REDDIT_NEW_URL    = "https://www.reddit.com/r/Pen_Swap/new.json"
REDDIT_SEARCH_URL = "https://www.reddit.com/r/Pen_Swap/search.json"

REDDIT_HEADERS = {
    "User-Agent": "FountainPenDealFinder/0.1 (image dataset collector; contact bhuh2020@gmail.com)"
}
DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

MIN_IMAGE_PIXELS = 100 * 100   # discard tiny thumbnails
MAX_IMAGE_BYTES  = 20 * 1024 * 1024  # 20 MB cap

BRAND_ALIASES: dict[str, list[str]] = {
    "Pilot":     ["pilot", "パイロット", "pilotpen"],
    "Namiki":    ["namiki", "ナミキ"],
    "Sailor":    ["sailor", "セーラー"],
    "Platinum":  ["platinum", "プラチナ"],
    "Nakaya":    ["nakaya", "ナカヤ"],
    "Pelikan":   ["pelikan", "ペリカン"],
    "Montblanc": ["montblanc", "mont blanc", "モンブラン"],
}


# ---------------------------------------------------------------------------
# Taxonomy matching (shared with scrape_training_data.py)
# ---------------------------------------------------------------------------

def load_taxonomy() -> list[dict]:
    rows = []
    if not TAXONOMY_PATH.exists():
        return rows
    with TAXONOMY_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({"brand": row["brand"].strip(),
                         "line": row["line"].strip(),
                         "alias": row["model_alias"].strip().lower()})
    return rows


def match_brand_line(text: str, taxonomy: list[dict]) -> tuple[str, str, str]:
    text_lower = text.lower()
    best_brand, best_line, best_score = "Unknown", "Unknown", 0.0
    for entry in taxonomy:
        alias = entry["alias"]
        if alias and alias in text_lower:
            score = len(alias) * 2 + (10 if entry["brand"].lower() in text_lower else 0)
            if score > best_score:
                best_score, best_brand, best_line = score, entry["brand"], entry["line"]
    if best_score >= 4:
        return best_brand, best_line, "high" if best_score >= 10 else "medium"
    for brand, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            if alias in text_lower:
                return brand, "Unknown", "medium"
    return "Unknown", "Unknown", "low"


# ---------------------------------------------------------------------------
# Image URL extraction from a Reddit post
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMGUR_DIRECT_RE  = re.compile(r"https?://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)", re.IGNORECASE)
REDD_IT_RE       = re.compile(r"https?://i\.redd\.it/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)", re.IGNORECASE)
GENERIC_IMG_RE   = re.compile(r"https?://\S+\.(?:jpg|jpeg|png|webp)(?:\?\S*)?", re.IGNORECASE)


def _reddit_preview_urls(post_data: dict) -> list[str]:
    """Extract Reddit's own hosted preview images (always available, decent quality)."""
    urls = []
    preview = post_data.get("preview") or {}
    for img in preview.get("images") or []:
        source = img.get("source") or {}
        url = source.get("url", "").replace("&amp;", "&")
        if url:
            urls.append(url)
    return urls


def _direct_image_urls(post_data: dict) -> list[str]:
    """Extract direct image links from the post URL and body."""
    urls = []
    post_url = post_data.get("url", "")
    selftext = post_data.get("selftext", "")
    combined = f"{post_url}\n{selftext}"

    # i.redd.it direct
    urls.extend(REDD_IT_RE.findall(combined))
    # i.imgur.com direct
    urls.extend(IMGUR_DIRECT_RE.findall(combined))
    # Generic image URLs in selftext
    urls.extend(GENERIC_IMG_RE.findall(selftext))

    # Direct post URL if it's an image
    if any(post_url.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
        urls.append(post_url)

    # Deduplicate preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_image_urls(post_data: dict) -> list[str]:
    """Return best-quality image URLs for a post, direct links first."""
    direct   = _direct_image_urls(post_data)
    previews = _reddit_preview_urls(post_data)
    # Prefer direct images; use previews as fallback or supplement
    all_urls = direct + [u for u in previews if u not in direct]
    return all_urls[:8]  # cap at 8 images per post


# ---------------------------------------------------------------------------
# Image download
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text[:40].strip("_") or "unknown"


def download_image(url: str, dest: Path, client: httpx.Client) -> bool:
    try:
        resp = client.get(url, headers=DOWNLOAD_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_IMAGE_BYTES:
            return False
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        w, h = img.size
        if w * h < MIN_IMAGE_PIXELS:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=90)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def load_manifest() -> set[str]:
    """Return set of already-downloaded image paths."""
    if not MANIFEST_PATH.exists():
        return set()
    paths = set()
    with MANIFEST_PATH.open(encoding="utf-8") as f:
        for line in f:
            try:
                paths.add(json.loads(line)["image_path"])
            except Exception:
                pass
    return paths


def append_manifest(entry: dict):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Reddit scraping
# ---------------------------------------------------------------------------

def _is_wts_post(title: str, flair: str) -> bool:
    title_lower = title.lower()
    flair_lower = (flair or "").lower()
    is_selling = any(t in title_lower for t in ("[wts]", "wts ", "wts:", "[h]", "for sale")) \
                 or "wts" in flair_lower or "closed" in flair_lower
    pen_terms = ["pen", "fountain", "pilot", "sailor", "platinum", "namiki", "nakaya",
                 "pelikan", "montblanc", "lamy", "twsbi", "kaweco", "parker"]
    return is_selling and any(t in title_lower for t in pen_terms)


def fetch_posts(client: httpx.Client, max_pages: int) -> list[dict]:
    posts = []
    seen = set()

    feeds = [
        (REDDIT_NEW_URL, {"limit": "100"}),
        (REDDIT_SEARCH_URL, {"q": "flair:Closed fountain pen pilot sailor",
                              "restrict_sr": "1", "sort": "new", "t": "year", "limit": "100"}),
    ]

    for url, base_params in feeds:
        after = None
        for page in range(1, max_pages + 1):
            params = dict(base_params)
            if after:
                params["after"] = after
            try:
                resp = client.get(url, params=params, headers=REDDIT_HEADERS, timeout=20)
                resp.raise_for_status()
                data = resp.json().get("data", {})
                children = data.get("children", [])
                if not children:
                    break
                for child in children:
                    pd = child.get("data", {})
                    permalink = pd.get("permalink", "")
                    if permalink not in seen:
                        seen.add(permalink)
                        posts.append(pd)
                print(f"  fetched page {page} ({len(children)} posts, {len(posts)} total)")
                after = data.get("after")
                if not after:
                    break
                time.sleep(2.0)
            except Exception as e:
                print(f"  [warn] page {page}: {e}")
                break

    return posts


# ---------------------------------------------------------------------------
# Main download loop
# ---------------------------------------------------------------------------

def collect(max_pages: int, min_confidence: str) -> None:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank = confidence_rank.get(min_confidence, 2)

    taxonomy = load_taxonomy()
    already_downloaded = load_manifest()
    new_images = 0

    print(f"[collect] Already downloaded: {len(already_downloaded)} images")
    print(f"[collect] Min confidence filter: {min_confidence}")

    with httpx.Client(timeout=20, follow_redirects=True) as reddit_client, \
         httpx.Client(timeout=15, follow_redirects=True) as img_client:

        print(f"[reddit] Fetching posts (up to {max_pages} pages per feed)…")
        posts = fetch_posts(reddit_client, max_pages)
        print(f"[reddit] {len(posts)} posts fetched")

        for post in posts:
            title = post.get("title", "")
            flair = post.get("link_flair_text", "")
            post_id = post.get("id", "")

            if not _is_wts_post(title, flair):
                continue

            brand, line, conf = match_brand_line(f"{title} {post.get('selftext','')}", taxonomy)
            if confidence_rank.get(conf, 0) < min_rank:
                continue

            image_urls = extract_image_urls(post)
            if not image_urls:
                continue

            brand_slug = _slug(brand)
            line_slug  = _slug(line)
            source_url = f"https://www.reddit.com{post.get('permalink','')}"

            for n, img_url in enumerate(image_urls):
                url_hash = hashlib.md5(img_url.encode()).hexdigest()[:8]
                filename = f"{post_id}_{n}_{url_hash}.jpg"
                dest = IMAGES_DIR / brand_slug / line_slug / filename
                rel_path = str(dest.relative_to(ROOT))

                if rel_path in already_downloaded:
                    continue

                ok = download_image(img_url, dest, img_client)
                if not ok:
                    continue

                entry = {
                    "image_path": rel_path,
                    "brand": brand,
                    "line": line,
                    "condition": "",
                    "label_confidence": conf,
                    "source": "r_pen_swap",
                    "source_url": source_url,
                    "image_url": img_url,
                    "post_id": post_id,
                    "post_title": title,
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                }
                append_manifest(entry)
                already_downloaded.add(rel_path)
                new_images += 1
                time.sleep(0.3)

    print(f"\n[done] Downloaded {new_images} new images")
    print(f"       Total in manifest: {len(already_downloaded)}")
    print(f"       Manifest: {MANIFEST_PATH}")
    print(f"       Images:   {IMAGES_DIR}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download pen images from r/Pen_Swap")
    parser.add_argument("--pages", type=int, default=15,
                        help="Reddit pages per feed to scrape (default: 15)")
    parser.add_argument("--min-confidence", choices=["high", "medium", "low"],
                        default="medium",
                        help="Only download images from posts with at least this label confidence")
    args = parser.parse_args()
    collect(args.pages, args.min_confidence)


if __name__ == "__main__":
    main()
