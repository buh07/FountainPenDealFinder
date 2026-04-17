#!/usr/bin/env python3
"""
Scrape sale data from Yahoo! JAPAN completed auctions and r/Pen_Swap.

Outputs a CSV to data/labeled/review/ for manual verification.
Once reviewed, run scripts/import_reviewed_sales.py to promote
approved rows into the training JSONL files.

Usage:
    python3 scripts/scrape_training_data.py
    python3 scripts/scrape_training_data.py --yahoo-pages 10 --pen-swap-pages 10
    python3 scripts/scrape_training_data.py --skip-yahoo
    python3 scripts/scrape_training_data.py --skip-pen-swap
"""

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "data" / "taxonomy" / "taxonomy_v1_seed.csv"
REVIEW_DIR = ROOT / "data" / "labeled" / "review"

# Approximate JPY/USD rate. Pen_Swap prices are USD; this is for reference only.
# The review CSV includes the original USD price so you can apply your own rate.
JPY_PER_USD = 150

YAHOO_CLOSED_URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"
YAHOO_KEYWORD = "万年筆"
# Brand-specific searches yield higher hit-rate for our target pens
YAHOO_BRAND_KEYWORDS = [
    "パイロット 万年筆",
    "セーラー 万年筆",
    "プラチナ 万年筆",
    "ナカヤ 万年筆",
    "ペリカン 万年筆",
    "モンブラン 万年筆",
    "ナミキ 万年筆",
]
REDDIT_NEW_URL = "https://www.reddit.com/r/Pen_Swap/new.json"
REDDIT_SEARCH_URL = "https://www.reddit.com/r/Pen_Swap/search.json"

# Minimum USD price to capture as a pen listing (filters out $6 shipping lines, etc.)
MIN_PEN_PRICE_USD = 40

CONDITION_PATTERNS = [
    # Numeric-suffix variants (A1/A2 = A, B1/B2 = B+, etc.) — check before bare letters
    (r"\bA\s*[12]?\s*(?:condition|cond|grade)?\b", "A"),
    (r"\bB\s*\+\s*(?:condition|cond|grade)?\b", "B+"),
    (r"\bB[12]\s*(?:condition|cond|grade)?\b", "B+"),
    (r"\bB\s*(?:condition|cond|grade)\b", "B"),
    (r"condition\s*[:\-]?\s*B\b", "B"),
    (r"\bC\s*(?:condition|cond|grade)?\b", "C"),
    (r"\b(?:mint|未使用)\b", "A"),
    (r"\bnear\s*mint\b", "A"),
    (r"\b(?:parts?|repair|ジャンク|junk)\b", "Parts/Repair"),
]

LOT_PATTERN = re.compile(r"\b(\d+)\s*(?:本|pens?|pieces?|items?|lot)\b", re.IGNORECASE)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
REDDIT_HEADERS = {
    "User-Agent": "FountainPenDealFinder/0.1 (personal research; contact bhuh2020@gmail.com)"
}

JST = ZoneInfo("Asia/Tokyo")


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

def load_taxonomy() -> list[dict]:
    rows = []
    if not TAXONOMY_PATH.exists():
        print(f"[warn] taxonomy not found at {TAXONOMY_PATH}", file=sys.stderr)
        return rows
    with TAXONOMY_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "brand": row["brand"].strip(),
                "line": row["line"].strip(),
                "alias": row["model_alias"].strip().lower(),
            })
    return rows


# Brand-name aliases (English + Japanese katakana) for brand-level fallback matching.
# These are checked when no specific model alias matches.
BRAND_ALIASES: dict[str, list[str]] = {
    "Pilot":      ["pilot", "パイロット", "pilotpen"],
    "Namiki":     ["namiki", "ナミキ", "並木"],
    "Sailor":     ["sailor", "セーラー", "sailorpen"],
    "Platinum":   ["platinum", "プラチナ", "白金"],
    "Nakaya":     ["nakaya", "ナカヤ", "中屋"],
    "Pelikan":    ["pelikan", "ペリカン"],
    "Montblanc":  ["montblanc", "mont blanc", "モンブラン", "meisterstück", "meisterstuck"],
}


def match_brand_line(text: str, taxonomy: list[dict]) -> tuple[str, str, str]:
    """Return (brand, line, confidence) for best taxonomy match.

    Priority:
      1. Specific model alias match → high confidence
      2. Brand-name alias match (English or Japanese) → medium confidence, line=Unknown
    """
    text_lower = text.lower()
    best_brand, best_line, best_score = "Unknown", "Unknown", 0.0

    for entry in taxonomy:
        alias = entry["alias"]
        if alias and alias in text_lower:
            score = len(alias) * 2 + (10 if entry["brand"].lower() in text_lower else 0)
            if score > best_score:
                best_score = score
                best_brand = entry["brand"]
                best_line = entry["line"]

    if best_score >= 4:
        confidence = "high" if best_score >= 10 else "medium"
        return best_brand, best_line, confidence

    # Brand-level fallback
    for brand, aliases in BRAND_ALIASES.items():
        for alias in aliases:
            if alias in text_lower:
                return brand, "Unknown", "medium"

    return "Unknown", "Unknown", "low"


def extract_condition(text: str) -> str:
    for pattern, grade in CONDITION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return grade
    return ""


def extract_lot_size(text: str) -> int:
    m = LOT_PATTERN.search(text)
    return int(m.group(1)) if m else 1


def extract_usd_price(text: str) -> float | None:
    m = re.search(r"\$\s*([0-9,]+(?:\.[0-9]{1,2})?)", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_jst_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Yahoo completed auctions  (reads __NEXT_DATA__ embedded JSON)
# ---------------------------------------------------------------------------

def _parse_yahoo_next_data(html: str, taxonomy: list[dict], now: datetime) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    nd_tag = soup.select_one("#__NEXT_DATA__")
    if not nd_tag:
        return []

    try:
        nd = json.loads(nd_tag.string or "")
    except json.JSONDecodeError:
        return []

    items = (
        nd.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("search", {})
        .get("items", {})
        .get("listing", {})
        .get("items", [])
    )

    rows = []
    for item in items:
        end_raw = item.get("endTime", "")
        end_dt = _parse_jst_iso(end_raw)
        if end_dt is None or end_dt > now:
            continue  # skip still-active auctions

        title = str(item.get("title") or "")
        auction_id = str(item.get("auctionId") or "")
        price = item.get("price")
        bid_count = item.get("bidCount")
        is_fixed = item.get("isFixedPrice", False)

        brand, line, conf = match_brand_line(title, taxonomy)

        # Yahoo itemCondition: "NEW" or "USED" — map to grades where possible
        raw_cond = str(item.get("itemCondition") or "")
        condition = extract_condition(title)
        if not condition:
            if raw_cond == "NEW":
                condition = "A"
            # USED → leave blank for manual review

        lot_size = extract_lot_size(title)
        url = f"https://page.auctions.yahoo.co.jp/jp/auction/{auction_id}"

        # thumbnail URL — Yahoo stores it under "thumbnail" or "image"
        thumbnail_url = str(item.get("thumbnail") or item.get("image") or "")

        rows.append({
            "source": "yahoo_auctions",
            "source_url": url,
            "raw_title": title,
            "raw_line": "",
            "extracted_brand": brand,
            "extracted_line": line,
            "extracted_condition": condition,
            "price_raw": f"¥{price:,}" if price else "",
            "price_jpy": price or "",
            "price_usd": "",
            "final_price_jpy": price or "",
            "bid_count": bid_count or "",
            "sold_indicators": "auction_ended" + ("|buy_now" if is_fixed else ""),
            "extraction_confidence": conf,
            "item_count": lot_size,
            "ended_at": end_raw,
            "scraped_at": now_iso(),
            "thumbnail_url": thumbnail_url,
            "visual_brand": "",
            "visual_line": "",
            "visual_confidence": "",
        })

    return rows


def _scrape_yahoo_keyword(
    client: httpx.Client,
    keyword: str,
    pages: int,
    taxonomy: list[dict],
    now: datetime,
    seen_ids: set[str],
) -> list[dict]:
    rows = []
    for page in range(1, pages + 1):
        # Yahoo pagination uses b= offset (b=1, b=51, b=101 …), NOT apg=
        b_offset = (page - 1) * 50 + 1
        params = {"p": keyword, "ei": "UTF-8", "b": str(b_offset), "s1": "end", "o1": "d"}
        try:
            resp = client.get(YAHOO_CLOSED_URL, params=params)
            resp.raise_for_status()
            page_rows = _parse_yahoo_next_data(resp.text, taxonomy, now)
            new = [r for r in page_rows if r["source_url"] not in seen_ids]
            for r in new:
                seen_ids.add(r["source_url"])
            rows.extend(new)
            print(f"    p{page} (b={b_offset}): {len(new)} new / {len(page_rows)} total")
            if not page_rows:
                break  # no more results
            time.sleep(1.5)
        except httpx.HTTPStatusError as e:
            print(f"    [warn] p{page} HTTP {e.response.status_code} — stopping keyword", file=sys.stderr)
            break
        except Exception as e:
            print(f"    [warn] p{page} error: {e} — stopping keyword", file=sys.stderr)
            break
    return rows


def scrape_yahoo_completed(pages: int = 8, brand_pages: int = 0) -> list[dict]:
    taxonomy = load_taxonomy()
    all_rows: list[dict] = []
    now = datetime.now(timezone.utc)
    seen_ids: set[str] = set()

    keywords = [(YAHOO_KEYWORD, pages)]
    if brand_pages > 0:
        keywords += [(kw, brand_pages) for kw in YAHOO_BRAND_KEYWORDS]

    with httpx.Client(timeout=25, follow_redirects=True, headers=REQUEST_HEADERS) as client:
        for keyword, n_pages in keywords:
            print(f"[yahoo] '{keyword}' — {n_pages} pages...")
            kw_rows = _scrape_yahoo_keyword(client, keyword, n_pages, taxonomy, now, seen_ids)
            all_rows.extend(kw_rows)
            print(f"  subtotal: {len(kw_rows)} rows")

    print(f"[yahoo] Grand total: {len(all_rows)} completed auction rows")
    return all_rows


# ---------------------------------------------------------------------------
# Reddit r/Pen_Swap
# ---------------------------------------------------------------------------

def _is_wts_post(title: str, flair: str) -> bool:
    """Return True if this post is a WTS (selling) fountain pen listing."""
    title_lower = title.lower()
    flair_lower = (flair or "").lower()

    # Must be a selling post (WTS/[WTS]/WTS:) or Closed (already sold)
    is_selling = (
        "[wts]" in title_lower
        or "wts " in title_lower
        or "wts:" in title_lower
        or "wts-" in title_lower
        or "[h]" in title_lower
        or "for sale" in title_lower
        or "wts" in flair_lower
        or "closed" in flair_lower
    )
    if not is_selling:
        return False

    # Must mention fountain pen brands or pen-related terms
    pen_terms = [
        "pen", "fountain", "fp", "nib",
        "pilot", "sailor", "platinum", "namiki", "nakaya",
        "pelikan", "montblanc", "lamy", "twsbi", "kaweco",
        "parker", "visconti", "omas", "conklin", "graf",
        "万年筆",
    ]
    return any(t in title_lower for t in pen_terms)


def _is_pen_line(line: str, taxonomy: list[dict]) -> bool:
    """Return True if a line looks like a pen listing (not just shipping/accessories)."""
    line_lower = line.lower()
    pen_terms = [
        "pen", "nib", "fp",
        "pilot", "sailor", "platinum", "namiki", "nakaya",
        "pelikan", "montblanc", "lamy", "twsbi", "kaweco",
        "parker", "visconti", "omas",
        "万年筆",
    ]
    # Reject lines that are clearly shipping/supplies
    shipping_terms = ["shipping", "ship", "conus", "international", "paypal", "venmo", "g&s", "f&f",
                      "pencil", "ink bottle", "notebook", "journal", "paper"]
    has_shipping_only = any(t in line_lower for t in shipping_terms) and not any(t in line_lower for t in pen_terms[:5])
    return any(t in line_lower for t in pen_terms) and not has_shipping_only


def _parse_pen_swap_post(post_data: dict, taxonomy: list[dict]) -> list[dict]:
    """Parse a single Pen_Swap post into one or more sale rows."""
    title = str(post_data.get("title") or "")
    selftext = str(post_data.get("selftext") or "")
    permalink = str(post_data.get("permalink") or "")
    flair = str(post_data.get("link_flair_text") or "")
    url = f"https://www.reddit.com{permalink}"
    created_utc = post_data.get("created_utc")
    post_date = (
        datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
        if created_utc else ""
    )

    post_is_closed = "closed" in flair.lower()
    strikethrough_re = re.compile(r"~~(.+?)~~")

    rows = []
    for raw_line in selftext.splitlines():
        raw_line = raw_line.strip()
        if len(raw_line) < 8:
            continue
        # Skip obvious header/boilerplate lines
        if raw_line.startswith(("---", "===", "###", "**", "Paypal", "PayPal", "Venmo", "All prices", "Prices")):
            continue

        usd_price = extract_usd_price(raw_line)
        if usd_price is None or usd_price < MIN_PEN_PRICE_USD:
            continue

        # Require some pen-related content on this line
        if not _is_pen_line(raw_line, taxonomy):
            continue

        # Sold detection
        sold_flags = []
        if post_is_closed:
            sold_flags.append("post_closed")
        if re.search(r"\bSOLD\b", raw_line, re.IGNORECASE):
            sold_flags.append("SOLD_text")
        if strikethrough_re.search(raw_line):
            sold_flags.append("strikethrough")
        if re.search(r"\bPENDING\b", raw_line, re.IGNORECASE):
            sold_flags.append("PENDING_text")
        if not sold_flags:
            sold_flags.append("listed_open")

        brand, line, conf = match_brand_line(f"{title} {raw_line}", taxonomy)
        condition = extract_condition(raw_line) or extract_condition(title)
        lot_size = extract_lot_size(raw_line)
        price_jpy = int(usd_price * JPY_PER_USD)
        is_sold = any(f in sold_flags for f in ("post_closed", "SOLD_text", "strikethrough"))

        rows.append({
            "source": "r_pen_swap",
            "source_url": url,
            "raw_title": title,
            "raw_line": raw_line[:200],
            "extracted_brand": brand,
            "extracted_line": line,
            "extracted_condition": condition,
            "price_raw": f"${usd_price:.0f}",
            "price_jpy": price_jpy,
            "price_usd": usd_price,
            "final_price_jpy": price_jpy if is_sold else "",
            "bid_count": "",
            "sold_indicators": "|".join(sold_flags),
            "extraction_confidence": conf,
            "item_count": lot_size,
            "ended_at": post_date,
            "scraped_at": now_iso(),
        })

    return rows


def _fetch_json(client: httpx.Client, url: str, params: dict) -> dict | None:
    try:
        resp = client.get(url, params=params, headers=REDDIT_HEADERS)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        print(f"  [warn] HTTP {e.response.status_code} — {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [warn] {e}", file=sys.stderr)
        return None


def scrape_pen_swap(max_pages: int = 10) -> list[dict]:
    """
    Fetch r/Pen_Swap posts via /new and /search (Closed flair).
    max_pages applies per feed (new + closed search).
    """
    print(f"[pen_swap] Scraping r/Pen_Swap (up to {max_pages} pages per feed)...")
    taxonomy = load_taxonomy()
    all_rows: list[dict] = []
    seen_permalinks: set[str] = set()

    def _process_posts(posts: list[dict]) -> int:
        new_rows = 0
        for post in posts:
            pd = post.get("data", {})
            permalink = pd.get("permalink", "")
            if permalink in seen_permalinks:
                continue
            seen_permalinks.add(permalink)
            title = pd.get("title", "")
            flair = pd.get("link_flair_text", "")
            if not _is_wts_post(title, flair):
                continue
            post_rows = _parse_pen_swap_post(pd, taxonomy)
            all_rows.extend(post_rows)
            new_rows += len(post_rows)
        return new_rows

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        # Feed 1: /new — most recent listings (includes selftext)
        print("  [feed] /new posts...")
        after = None
        for page in range(1, max_pages + 1):
            params: dict = {"limit": "100"}
            if after:
                params["after"] = after
            data = _fetch_json(client, REDDIT_NEW_URL, params)
            if not data:
                break
            posts = data.get("data", {}).get("children", [])
            if not posts:
                break
            n = _process_posts(posts)
            print(f"    page {page}: {len(posts)} posts → {n} pen rows")
            after = data.get("data", {}).get("after")
            if not after:
                break
            time.sleep(2.5)

        # Feed 2: search for Closed posts (confirmed sold)
        print("  [feed] Closed flair search...")
        after = None
        for page in range(1, max_pages + 1):
            params = {
                "q": "flair:Closed fountain pen pilot sailor montblanc",
                "restrict_sr": "1",
                "sort": "new",
                "limit": "100",
                "t": "year",
            }
            if after:
                params["after"] = after
            data = _fetch_json(client, REDDIT_SEARCH_URL, params)
            if not data:
                break
            posts = data.get("data", {}).get("children", [])
            if not posts:
                break
            n = _process_posts(posts)
            print(f"    page {page}: {len(posts)} posts → {n} pen rows")
            after = data.get("data", {}).get("after")
            if not after:
                break
            time.sleep(2.5)

    print(f"[pen_swap] Total: {len(all_rows)} rows from {len(seen_permalinks)} posts visited")
    return all_rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

REVIEW_COLUMNS = [
    # ---- User fills these ----
    "review_status",       # keep | discard | fix  (leave blank = unreviewed)
    "notes",               # any free-text notes
    # ---- Override if extraction was wrong ----
    "override_brand",
    "override_line",
    "override_condition",
    "override_price_jpy",
    # ---- Scraped / extracted ----
    "source",
    "source_url",
    "raw_title",
    "raw_line",            # pen_swap only: specific line from post body
    "extracted_brand",
    "extracted_line",
    "extracted_condition",
    "extraction_confidence",  # high / medium / low
    "price_raw",           # original scraped price string
    "price_usd",           # pen_swap: USD price before conversion
    "price_jpy",           # price in JPY (pen_swap: USD×150; yahoo: scraped)
    "final_price_jpy",     # confirmed sold price (same as price_jpy when sold)
    "bid_count",           # yahoo only
    "item_count",
    "sold_indicators",     # evidence of sale
    "ended_at",
    "scraped_at",
    "thumbnail_url",
    "visual_brand",
    "visual_line",
    "visual_confidence",
]


def write_review_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {col: "" for col in REVIEW_COLUMNS}
            out.update({k: v for k, v in row.items() if k in REVIEW_COLUMNS})
            writer.writerow(out)
    print(f"[output] Wrote {len(rows)} rows → {path}")


def write_summary(rows: list[dict], path: Path) -> None:
    summary_path = path.with_suffix(".md")

    yahoo_rows = [r for r in rows if r.get("source") == "yahoo_auctions"]
    ps_rows = [r for r in rows if r.get("source") == "r_pen_swap"]
    sold_ps = [r for r in ps_rows if any(
        s in r.get("sold_indicators", "") for s in ("post_closed", "SOLD_text", "strikethrough")
    )]
    high_conf = [r for r in rows if r.get("extraction_confidence") == "high"]
    unknown = [r for r in rows if r.get("extracted_brand") == "Unknown"]

    yahoo_brands = Counter(r["extracted_brand"] for r in yahoo_rows if r["extracted_brand"] != "Unknown")
    ps_brands = Counter(r["extracted_brand"] for r in ps_rows if r["extracted_brand"] != "Unknown")

    lines = [
        "# Scraped Sales — Review Guide",
        "",
        f"**Scraped at:** {now_iso()}",
        f"**Total rows:** {len(rows)}  ({len(yahoo_rows)} Yahoo + {len(ps_rows)} Pen_Swap)",
        f"**High-confidence extractions:** {len(high_conf)}",
        f"**Unknown brand (needs manual ID):** {len(unknown)}",
        "",
        "---",
        "",
        "## How to Review",
        "",
        "Open the companion `.csv` file in Excel, Numbers, or Google Sheets.",
        "Fill in `review_status` for each row:",
        "",
        "| Value | Meaning |",
        "|-------|---------|",
        "| `keep` | Data looks correct — add to training set |",
        "| `discard` | Bad data, wrong item type, duplicate |",
        "| `fix` | Data needs correction — fill in `override_*` columns |",
        "| *(blank)* | Not yet reviewed |",
        "",
        "**Column-by-column guide:**",
        "",
        "- **`extraction_confidence`** — `high` means the brand/model alias was found in the",
        "  taxonomy; `low` means it wasn't recognized. `low` rows almost always need `override_brand`",
        "  and `override_line` filled in, or should be discarded.",
        "",
        "- **`sold_indicators`** — what evidence suggests the item sold:",
        "  - `auction_ended` — Yahoo auction end time is in the past ✓",
        "  - `post_closed` — Pen_Swap post has 'Closed' flair (seller marked complete) ✓",
        "  - `SOLD_text` — line contains the word SOLD ✓",
        "  - `strikethrough` — line is ~~struck through~~ in markdown ✓",
        "  - `listed_open` — item is listed but no confirmed sale marker — treat with caution",
        "",
        "- **`price_jpy`** — for Yahoo this is the final bid; for Pen_Swap this is USD × 150.",
        "  - Adjust `override_price_jpy` if you know a better rate was in effect.",
        "  - `price_usd` always has the original USD value for Pen_Swap rows.",
        "",
        "- **`final_price_jpy`** — populated only when sale is confirmed. For `listed_open` rows",
        "  this is blank — do NOT use them as sold-price training data unless you confirm the sale.",
        "",
        "- **`item_count`** — lot size detected from text (e.g., 'set of 2'). Check lots carefully;",
        "  they need per-pen price division before use.",
        "",
        "**When done, run:**",
        "```",
        f"python3 scripts/import_reviewed_sales.py --input {path}",
        "```",
        "",
        "---",
        "",
        "## Stats",
        "",
        f"### Yahoo Auctions ({len(yahoo_rows)} completed auction rows)",
        "",
    ]

    if yahoo_brands:
        lines.append("Brands recognized:")
        for brand, count in yahoo_brands.most_common(10):
            lines.append(f"- **{brand}**: {count}")
    else:
        lines.append(
            "_No brands recognized from Yahoo. The taxonomy aliases may not match the Japanese titles._  \n"
            "_Try adding hiragana/katakana aliases to `data/taxonomy/taxonomy_v1_seed.csv`._"
        )

    lines += [
        "",
        f"### r/Pen_Swap ({len(ps_rows)} rows, {len(sold_ps)} with confirmed sale indicators)",
        "",
    ]

    if ps_brands:
        lines.append("Brands recognized:")
        for brand, count in ps_brands.most_common(10):
            lines.append(f"- **{brand}**: {count}")
    else:
        lines.append("_No brands recognized from Pen_Swap posts._")

    lines += [
        "",
        "---",
        "",
        "## JPY/USD Conversion Note",
        "",
        f"Pen_Swap prices were converted at **{JPY_PER_USD} JPY/USD** (approximate).",
        "Check actual rates for the sale date if precision matters.",
        "The original USD price is always preserved in `price_usd`.",
        "",
        "For the model, `ask_price_jpy` in the training data represents the **Japanese market",
        "source price** (what you'd pay to buy in Japan), while `sold_price_jpy` is the",
        "**Western resale price** (what it sold for on Pen_Swap).",
        "",
        "Yahoo rows give you the Japanese market price (final auction bid).",
        "Pen_Swap rows give you the Western resale price.",
        "Both are useful — just make sure they go into the right JSONL file.",
    ]

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[output] Wrote guide → {summary_path}")


# ---------------------------------------------------------------------------
# Visual classification enrichment
# ---------------------------------------------------------------------------

def _enrich_with_visual_labels(rows: list[dict]) -> None:
    """Fill visual_brand/visual_line/visual_confidence on Yahoo rows that have thumbnail_url."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "apps" / "api"))
        from app.services.pen_image_classifier import classify_image_url
    except ImportError as e:
        print(f"[visual] Cannot import classifier: {e}. Skipping visual labeling.")
        return

    yahoo_with_thumb = [r for r in rows
                        if r.get("source") == "yahoo_auctions" and r.get("thumbnail_url")]
    print(f"[visual] Classifying {len(yahoo_with_thumb)} Yahoo thumbnails…")
    for i, row in enumerate(yahoo_with_thumb, 1):
        result = classify_image_url(row["thumbnail_url"])
        row["visual_brand"]      = result["brand"]
        row["visual_line"]       = result["line"]
        row["visual_confidence"] = result["confidence"]
        if (i % 20) == 0:
            print(f"  {i}/{len(yahoo_with_thumb)} classified")
        import time as _time
        _time.sleep(0.2)
    print(f"[visual] Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape fountain pen sale data for training")
    parser.add_argument("--yahoo-pages", type=int, default=20,
                        help="Yahoo general '万年筆' pages (default: 20, ~50 items/page)")
    parser.add_argument("--yahoo-brand-pages", type=int, default=15,
                        help="Yahoo pages per brand keyword (default: 15; 0 to skip brand searches)")
    parser.add_argument("--pen-swap-pages", type=int, default=20,
                        help="Reddit pages per feed (default: 20, ~100 posts/page)")
    parser.add_argument("--skip-yahoo", action="store_true")
    parser.add_argument("--skip-pen-swap", action="store_true")
    parser.add_argument("--classify-images", action="store_true",
                        help="Run visual classifier on Yahoo thumbnail_urls to fill visual_brand/line")
    parser.add_argument("--output", type=str, default="",
                        help="Output CSV path (default: auto-named by date)")
    args = parser.parse_args()

    all_rows: list[dict] = []

    if not args.skip_yahoo:
        all_rows.extend(scrape_yahoo_completed(pages=args.yahoo_pages, brand_pages=args.yahoo_brand_pages))
    if not args.skip_pen_swap:
        all_rows.extend(scrape_pen_swap(max_pages=args.pen_swap_pages))

    if args.classify_images:
        _enrich_with_visual_labels(all_rows)

    if not all_rows:
        print("No rows scraped. Check warnings above.")
        sys.exit(1)

    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(args.output) if args.output else REVIEW_DIR / f"scraped_sales_{date_str}.csv"

    write_review_csv(all_rows, out_path)
    write_summary(all_rows, out_path)

    print(f"\nDone — {len(all_rows)} rows total.")
    print(f"Review: {out_path}")
    print(f"Then:   python3 scripts/import_reviewed_sales.py --input {out_path}")


if __name__ == "__main__":
    main()
