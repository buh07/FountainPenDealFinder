#!/usr/bin/env python3
"""
Interactive web-based reviewer for scraped fountain pen sale data.

Loads the scraped CSV, shows each listing one at a time, and writes
confirmed rows to data/labeled/reviewed/checked_sales.jsonl.

Usage:
    python3 scripts/review_scraped_data.py
    python3 scripts/review_scraped_data.py --input data/labeled/review/scraped_sales_all_20260416.csv
    python3 scripts/review_scraped_data.py --port 8787

Keyboard shortcuts in the browser:
    K / Enter  →  Keep
    D          →  Discard
    S          →  Skip (review later)
    ←  →       →  Navigate to previous / next reviewed row
"""

import argparse
import csv
import json
import os
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

try:
    from deep_translator import GoogleTranslator
    _translator = GoogleTranslator(source="ja", target="en")
    TRANSLATION_AVAILABLE = True
except Exception:
    TRANSLATION_AVAILABLE = False

ROOT = Path(__file__).resolve().parent.parent
REVIEW_DIR = ROOT / "data" / "labeled" / "review"
REVIEWED_DIR = ROOT / "data" / "labeled" / "reviewed"
CHECKED_JSONL = REVIEWED_DIR / "checked_sales.jsonl"

BRANDS = ["Pilot", "Namiki", "Sailor", "Platinum", "Nakaya", "Pelikan", "Montblanc", "Other", "Unknown"]
CONDITIONS = ["A", "B+", "B", "C", "Parts/Repair", "Unknown"]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class ReviewState:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self.progress_path = csv_path.parent / f".progress_{csv_path.stem}.json"
        self.translation_cache_path = csv_path.parent / f".translations_{csv_path.stem}.json"
        self.rows: list[dict] = []
        self.decisions: dict[int, str] = {}   # idx -> "keep" | "discard" | "skip"
        self.translations: dict[str, str] = {}  # original text -> english translation
        self._load_csv()
        self._load_progress()
        self._load_translation_cache()

    def _load_csv(self):
        with self.csv_path.open(encoding="utf-8") as f:
            self.rows = list(csv.DictReader(f))
        print(f"Loaded {len(self.rows)} rows from {self.csv_path.name}")

    def _load_progress(self):
        if self.progress_path.exists():
            data = json.loads(self.progress_path.read_text())
            self.decisions = {int(k): v for k, v in data.get("decisions", {}).items()}
            print(f"Resumed: {len(self.decisions)} rows already reviewed")

    def _load_translation_cache(self):
        if self.translation_cache_path.exists():
            self.translations = json.loads(self.translation_cache_path.read_text())

    def _save_translation_cache(self):
        self.translation_cache_path.write_text(json.dumps(self.translations, ensure_ascii=False, indent=2))

    def translate(self, text: str) -> str:
        if not text or not TRANSLATION_AVAILABLE:
            return ""
        # Return cached result if available
        if text in self.translations:
            return self.translations[text]
        # Only attempt translation for text containing Japanese characters
        has_japanese = any("\u3000" <= c <= "\u9fff" or "\uff00" <= c <= "\uffef" for c in text)
        if not has_japanese:
            return ""
        try:
            result = _translator.translate(text[:500])  # API limit guard
            self.translations[text] = result or ""
            self._save_translation_cache()
            return result or ""
        except Exception:
            return ""

    def save_progress(self):
        self.progress_path.write_text(
            json.dumps({"input": str(self.csv_path), "decisions": self.decisions}, indent=2)
        )

    def next_pending_index(self, after: int = -1) -> int | None:
        for i in range(after + 1, len(self.rows)):
            if i not in self.decisions:
                return i
        return None

    def stats(self) -> dict:
        kept = sum(1 for v in self.decisions.values() if v == "keep")
        discarded = sum(1 for v in self.decisions.values() if v == "discard")
        skipped = sum(1 for v in self.decisions.values() if v == "skip")
        remaining = len(self.rows) - len(self.decisions)
        return {
            "total": len(self.rows),
            "reviewed": len(self.decisions),
            "kept": kept,
            "discarded": discarded,
            "skipped": skipped,
            "remaining": remaining,
        }

    def row_payload(self, idx: int) -> dict:
        if idx < 0 or idx >= len(self.rows):
            return {}
        r = self.rows[idx]
        return {
            "idx": idx,
            "decision": self.decisions.get(idx),
            "source": r.get("source", ""),
            "source_url": r.get("source_url", ""),
            "raw_title": r.get("raw_title", ""),
            "raw_line": r.get("raw_line", ""),
            "extracted_brand": r.get("extracted_brand", ""),
            "extracted_line": r.get("extracted_line", ""),
            "extracted_condition": r.get("extracted_condition", ""),
            "extraction_confidence": r.get("extraction_confidence", ""),
            "price_raw": r.get("price_raw", ""),
            "price_jpy": r.get("price_jpy", ""),
            "price_usd": r.get("price_usd", ""),
            "final_price_jpy": r.get("final_price_jpy", ""),
            "bid_count": r.get("bid_count", ""),
            "item_count": r.get("item_count", "1"),
            "sold_indicators": r.get("sold_indicators", ""),
            "ended_at": r.get("ended_at", ""),
        }


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def append_checked(row_payload: dict, overrides: dict):
    REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
    brand = overrides.get("brand") or row_payload["extracted_brand"]
    line = overrides.get("line") or row_payload["extracted_line"]
    condition = overrides.get("condition") or row_payload["extracted_condition"] or "Unknown"
    notes = overrides.get("notes", "")

    try:
        price_jpy = int(str(overrides.get("price_jpy") or row_payload["price_jpy"] or 0).replace(",", ""))
    except ValueError:
        price_jpy = 0
    try:
        final_price_jpy = int(str(overrides.get("final_price_jpy") or row_payload["final_price_jpy"] or price_jpy).replace(",", ""))
    except ValueError:
        final_price_jpy = price_jpy
    try:
        item_count = int(str(overrides.get("item_count") or row_payload["item_count"] or 1))
    except ValueError:
        item_count = 1
    try:
        bid_count = int(str(row_payload.get("bid_count") or 0))
    except ValueError:
        bid_count = 0

    source = row_payload["source"]
    reviewed_at = datetime.now(timezone.utc).isoformat()

    if source == "yahoo_auctions":
        record = {
            "source": "yahoo_auctions",
            "brand": brand,
            "line": line,
            "condition_grade": condition,
            "current_price_jpy": price_jpy,
            "bid_count": bid_count,
            "hours_to_end": 0,
            "final_price_jpy": final_price_jpy,
            "item_count": item_count,
            "ended_at": row_payload.get("ended_at", ""),
            "_source_url": row_payload.get("source_url", ""),
            "_raw_title": row_payload.get("raw_title", ""),
            "_notes": notes,
            "_reviewed_at": reviewed_at,
        }
    else:
        try:
            price_usd = float(str(row_payload.get("price_usd") or 0))
        except ValueError:
            price_usd = 0.0
        record = {
            "source": "r_pen_swap",
            "brand": brand,
            "line": line,
            "condition_grade": condition,
            "ask_price_jpy": price_jpy,
            "sold_price_jpy": final_price_jpy,
            "price_usd": price_usd,
            "item_count": item_count,
            "sold_at": row_payload.get("ended_at", ""),
            "_source_url": row_payload.get("source_url", ""),
            "_raw_title": row_payload.get("raw_title", ""),
            "_notes": notes,
            "_reviewed_at": reviewed_at,
        }

    with CHECKED_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI()
state: ReviewState = None  # set in main()


class DecideRequest(BaseModel):
    idx: int
    decision: str           # "keep" | "discard" | "skip"
    brand: str = ""
    line: str = ""
    condition: str = ""
    price_jpy: str = ""
    final_price_jpy: str = ""
    item_count: str = "1"
    notes: str = ""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


@app.get("/api/stats")
def api_stats():
    return state.stats()


@app.get("/api/row/{idx}")
def api_row(idx: int):
    if idx < 0 or idx >= len(state.rows):
        raise HTTPException(404, "Row not found")
    return state.row_payload(idx)


@app.get("/api/next")
def api_next(after: int = -1):
    idx = state.next_pending_index(after)
    if idx is None:
        return {"idx": None, "done": True}
    return {**state.row_payload(idx), "done": False}


@app.post("/api/decide")
def api_decide(req: DecideRequest):
    if req.idx < 0 or req.idx >= len(state.rows):
        raise HTTPException(400, "Invalid idx")
    if req.decision not in ("keep", "discard", "skip"):
        raise HTTPException(400, "decision must be keep | discard | skip")

    state.decisions[req.idx] = req.decision
    state.save_progress()

    if req.decision == "keep":
        payload = state.row_payload(req.idx)
        append_checked(payload, {
            "brand": req.brand,
            "line": req.line,
            "condition": req.condition,
            "price_jpy": req.price_jpy,
            "final_price_jpy": req.final_price_jpy,
            "item_count": req.item_count,
            "notes": req.notes,
        })

    next_idx = state.next_pending_index(req.idx)
    stats = state.stats()
    return {"ok": True, "next_idx": next_idx, "stats": stats}


@app.get("/api/brands")
def api_brands():
    return BRANDS


@app.get("/api/conditions")
def api_conditions():
    return CONDITIONS


@app.get("/api/translate")
def api_translate(text: str = ""):
    translation = state.translate(text)
    return {"original": text, "translation": translation, "available": TRANSLATION_AVAILABLE}


# ---------------------------------------------------------------------------
# Embedded HTML
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pen Sale Reviewer</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f0f2f5; color: #1a1a2e; height: 100vh; display: flex;
         flex-direction: column; overflow: hidden; }

  /* ── Top bar ── */
  #topbar { background: #1a1a2e; color: #e2e8f0; padding: 10px 20px;
            display: flex; align-items: center; gap: 20px; flex-shrink: 0; }
  #topbar h1 { font-size: 16px; font-weight: 600; letter-spacing: 0.5px; }
  #progress-wrap { flex: 1; display: flex; align-items: center; gap: 12px; }
  #progress-bar { flex: 1; height: 6px; background: #374151; border-radius: 3px; overflow: hidden; }
  #progress-fill { height: 100%; background: #10b981; border-radius: 3px;
                   transition: width 0.3s ease; width: 0%; }
  #progress-text { font-size: 13px; white-space: nowrap; color: #94a3b8; }
  .stat-pill { font-size: 12px; padding: 2px 10px; border-radius: 999px; font-weight: 600; }
  .stat-kept   { background: #065f46; color: #6ee7b7; }
  .stat-disc   { background: #7f1d1d; color: #fca5a5; }
  .stat-skip   { background: #374151; color: #9ca3af; }
  #shortcut-hint { font-size: 11px; color: #64748b; }

  /* ── Main area ── */
  #main { flex: 1; display: flex; gap: 0; overflow: hidden; }

  /* ── Left panel: raw data ── */
  #left { flex: 1; padding: 20px 24px; overflow-y: auto;
          border-right: 1px solid #e2e8f0; background: #fff; }
  #source-badges { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
  .badge { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 4px;
           text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-yahoo  { background: #fef3c7; color: #92400e; }
  .badge-reddit { background: #fee2e2; color: #991b1b; }
  .badge-high   { background: #d1fae5; color: #065f46; }
  .badge-medium { background: #fef9c3; color: #854d0e; }
  .badge-low    { background: #fce7f3; color: #9d174d; }
  .badge-sold   { background: #dbeafe; color: #1e40af; }
  .badge-open   { background: #f3f4f6; color: #6b7280; }

  #raw-title { font-size: 18px; font-weight: 600; line-height: 1.5;
               margin-bottom: 10px; color: #0f172a; word-break: break-word; }
  #raw-line { font-size: 13px; color: #475569; background: #f8fafc;
              border-left: 3px solid #cbd5e1; padding: 8px 12px;
              border-radius: 0 6px 6px 0; margin-bottom: 12px;
              word-break: break-word; white-space: pre-wrap; }
  #raw-line:empty { display: none; }

  .meta-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px;
               font-size: 13px; margin-bottom: 16px; }
  .meta-label { color: #94a3b8; font-weight: 500; white-space: nowrap; }
  .meta-value { color: #334155; }
  .meta-value a { color: #3b82f6; text-decoration: none; }
  .meta-value a:hover { text-decoration: underline; }

  /* ── Right panel: edit form ── */
  #right { width: 340px; padding: 20px 20px; overflow-y: auto;
           background: #fafafa; flex-shrink: 0; }
  #right h2 { font-size: 13px; font-weight: 700; color: #64748b;
              text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 16px; }
  .field { margin-bottom: 14px; }
  .field label { display: block; font-size: 12px; font-weight: 600;
                 color: #64748b; margin-bottom: 4px; text-transform: uppercase;
                 letter-spacing: 0.5px; }
  .field select, .field input, .field textarea {
    width: 100%; padding: 8px 10px; border: 1px solid #e2e8f0;
    border-radius: 6px; font-size: 14px; color: #0f172a; background: #fff;
    transition: border-color 0.15s; }
  .field select:focus, .field input:focus, .field textarea:focus {
    outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,0.1); }
  .field textarea { resize: vertical; min-height: 60px; font-family: inherit; }

  .modified { border-color: #f59e0b !important; background: #fffbeb !important; }

  #decision-banner { padding: 10px 14px; border-radius: 8px; font-size: 13px;
                     font-weight: 600; margin-bottom: 16px; text-align: center; display: none; }
  .banner-keep    { background: #d1fae5; color: #065f46; }
  .banner-discard { background: #fee2e2; color: #991b1b; }
  .banner-skip    { background: #f1f5f9; color: #64748b; }

  /* ── Bottom action bar ── */
  #actions { background: #fff; border-top: 1px solid #e2e8f0;
             padding: 12px 20px; display: flex; align-items: center;
             gap: 10px; flex-shrink: 0; }
  .btn { padding: 9px 20px; border: none; border-radius: 8px; font-size: 14px;
         font-weight: 600; cursor: pointer; transition: all 0.15s;
         display: flex; align-items: center; gap: 6px; }
  .btn:active { transform: scale(0.97); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
  #btn-keep    { background: #10b981; color: #fff; flex: 1; justify-content: center; }
  #btn-keep:hover:not(:disabled) { background: #059669; }
  #btn-discard { background: #ef4444; color: #fff; flex: 1; justify-content: center; }
  #btn-discard:hover:not(:disabled) { background: #dc2626; }
  #btn-skip    { background: #e2e8f0; color: #475569; }
  #btn-skip:hover:not(:disabled) { background: #cbd5e1; }
  #btn-prev    { background: #e2e8f0; color: #475569; }
  #btn-prev:hover:not(:disabled) { background: #cbd5e1; }
  #btn-next-reviewed { background: #e2e8f0; color: #475569; }
  #btn-next-reviewed:hover:not(:disabled) { background: #cbd5e1; }

  .spacer { flex: 1; }

  /* ── Done screen ── */
  #done-screen { display: none; flex-direction: column; align-items: center;
                 justify-content: center; flex: 1; gap: 16px; padding: 40px; }
  #done-screen h2 { font-size: 28px; color: #0f172a; }
  #done-screen p  { font-size: 16px; color: #64748b; text-align: center; }
  #done-screen code { font-size: 13px; background: #f1f5f9; padding: 8px 14px;
                      border-radius: 6px; display: block; }

  /* ── Loading ── */
  #loading { display: flex; align-items: center; justify-content: center;
             flex: 1; font-size: 16px; color: #94a3b8; }
</style>
</head>
<body>

<div id="topbar">
  <h1>🖋 Pen Sale Reviewer</h1>
  <div id="progress-wrap">
    <div id="progress-bar"><div id="progress-fill"></div></div>
    <span id="progress-text">Loading…</span>
  </div>
  <span class="stat-pill stat-kept"  id="s-kept">✓ 0</span>
  <span class="stat-pill stat-disc"  id="s-disc">✗ 0</span>
  <span class="stat-pill stat-skip"  id="s-skip">↷ 0</span>
  <span id="shortcut-hint">K=Keep · D=Discard · S=Skip · ←→=Navigate</span>
</div>

<div id="loading">Loading data…</div>

<div id="main" style="display:none">
  <!-- LEFT: raw listing data -->
  <div id="left">
    <div id="source-badges"></div>
    <div id="raw-title"></div>
    <div id="raw-line"></div>
    <div class="meta-grid" id="meta-grid"></div>
  </div>

  <!-- RIGHT: editable fields -->
  <div id="right">
    <h2>Confirm Data</h2>
    <div id="decision-banner"></div>

    <div class="field">
      <label>Brand</label>
      <select id="f-brand"></select>
    </div>
    <div class="field">
      <label>Line / Model</label>
      <input id="f-line" type="text" placeholder="e.g. Custom 743">
    </div>
    <div class="field">
      <label>Condition</label>
      <select id="f-condition"></select>
    </div>
    <div class="field">
      <label id="price-label">Price (JPY)</label>
      <input id="f-price" type="number" min="0" step="100">
    </div>
    <div class="field" id="final-price-field">
      <label>Final / Sold Price (JPY)</label>
      <input id="f-final-price" type="number" min="0" step="100">
    </div>
    <div class="field">
      <label>Item Count</label>
      <input id="f-count" type="number" min="1" value="1">
    </div>
    <div class="field">
      <label>Notes</label>
      <textarea id="f-notes" placeholder="Optional notes…"></textarea>
    </div>
  </div>
</div>

<div id="done-screen">
  <h2>🎉 All rows reviewed!</h2>
  <p id="done-stats"></p>
  <p>Checked data written to:<br>
     <code>data/labeled/reviewed/checked_sales.jsonl</code></p>
  <p>To add to training data, run:<br>
     <code>python3 scripts/merge_checked_to_training.py</code></p>
</div>

<div id="actions">
  <button class="btn" id="btn-prev"         onclick="navigate(-1)">← Prev</button>
  <button class="btn" id="btn-skip"          onclick="decide('skip')">↷ Skip</button>
  <div class="spacer"></div>
  <button class="btn" id="btn-discard"       onclick="decide('discard')">✗ Discard</button>
  <button class="btn" id="btn-keep"          onclick="decide('keep')">✓ Keep</button>
  <div class="spacer"></div>
  <button class="btn" id="btn-next-reviewed" onclick="navigate(1)">Next →</button>
</div>

<script>
const BRANDS     = [];
const CONDITIONS = [];

let currentIdx   = null;   // index of currently displayed row
let history      = [];     // indices visited, for back-navigation
let historyPos   = -1;
let stats        = {};

// ── Initialise ──────────────────────────────────────────────────────────────

async function init() {
  const [brands, conds, st] = await Promise.all([
    fetch('/api/brands').then(r => r.json()),
    fetch('/api/conditions').then(r => r.json()),
    fetch('/api/stats').then(r => r.json()),
  ]);

  BRANDS.push(...brands);
  CONDITIONS.push(...conds);

  const bsel = document.getElementById('f-brand');
  brands.forEach(b => { const o = document.createElement('option'); o.value = o.textContent = b; bsel.appendChild(o); });

  const csel = document.getElementById('f-condition');
  conds.forEach(c => { const o = document.createElement('option'); o.value = o.textContent = c; csel.appendChild(o); });

  updateStats(st);
  await loadNext(-1);
}

// ── Data loading ─────────────────────────────────────────────────────────────

async function loadRow(idx) {
  const data = await fetch(`/api/row/${idx}`).then(r => r.json());
  renderRow(data);
}

async function loadNext(afterIdx) {
  const data = await fetch(`/api/next?after=${afterIdx}`).then(r => r.json());
  if (data.done || data.idx === null) {
    showDone();
    return;
  }
  renderRow(data);
}

// ── Render ───────────────────────────────────────────────────────────────────

function renderRow(data) {
  currentIdx = data.idx;

  // Push to history
  if (historyPos < history.length - 1) history = history.slice(0, historyPos + 1);
  if (history[history.length - 1] !== currentIdx) {
    history.push(currentIdx);
    historyPos = history.length - 1;
  }

  document.getElementById('loading').style.display = 'none';
  document.getElementById('done-screen').style.display = 'none';
  document.getElementById('main').style.display = 'flex';
  document.getElementById('actions').style.display = 'flex';

  // Source badges
  const isYahoo   = data.source === 'yahoo_auctions';
  const conf      = data.extraction_confidence || 'low';
  const sold_inds = data.sold_indicators || '';
  const isSold    = sold_inds.includes('auction_ended') || sold_inds.includes('post_closed') ||
                    sold_inds.includes('SOLD_text') || sold_inds.includes('strikethrough');

  document.getElementById('source-badges').innerHTML = `
    <span class="badge ${isYahoo ? 'badge-yahoo' : 'badge-reddit'}">${isYahoo ? 'Yahoo Auctions' : 'r/Pen_Swap'}</span>
    <span class="badge badge-${conf}">${conf} confidence</span>
    ${isSold ? '<span class="badge badge-sold">CONFIRMED SOLD</span>' : '<span class="badge badge-open">UNCONFIRMED</span>'}
    <span style="margin-left:auto;font-size:12px;color:#94a3b8">#${data.idx + 1}</span>
  `;

  const titleEl = document.getElementById('raw-title');
  titleEl.textContent = data.raw_title || '(no title)';

  // Translation: clear previous, then fetch async
  let translEl = document.getElementById('raw-title-translation');
  if (!translEl) {
    translEl = document.createElement('div');
    translEl.id = 'raw-title-translation';
    translEl.style.cssText = 'font-size:14px;color:#059669;margin:-6px 0 12px 0;font-style:italic;min-height:1.4em;';
    titleEl.insertAdjacentElement('afterend', translEl);
  }
  translEl.textContent = '…';
  if (data.source === 'yahoo_auctions' && data.raw_title) {
    fetch(`/api/translate?text=${encodeURIComponent(data.raw_title)}`)
      .then(r => r.json())
      .then(t => { translEl.textContent = t.translation || ''; })
      .catch(() => { translEl.textContent = ''; });
  } else {
    translEl.textContent = '';
  }

  const rawLine = document.getElementById('raw-line');
  rawLine.textContent = data.raw_line || '';

  // Meta grid
  const urlShort = (data.source_url || '').replace('https://','').slice(0, 60) + (data.source_url.length > 63 ? '…' : '');
  const price    = data.price_raw || (data.price_jpy ? `¥${Number(data.price_jpy).toLocaleString()}` : '—');
  const soldPrice= data.final_price_jpy ? `¥${Number(data.final_price_jpy).toLocaleString()}` : '—';
  const bids     = data.bid_count || '—';
  const ended    = data.ended_at ? data.ended_at.slice(0, 10) : '—';
  const soldText = sold_inds.split('|').join(' · ') || '—';
  let meta = `
    <span class="meta-label">URL</span>
    <span class="meta-value"><a href="${data.source_url}" target="_blank" rel="noopener">${urlShort}</a></span>
    <span class="meta-label">Listed price</span>
    <span class="meta-value">${price}${data.price_usd ? ` ($${data.price_usd})` : ''}</span>
    <span class="meta-label">Final / sold</span>
    <span class="meta-value">${soldPrice}</span>
    <span class="meta-label">Bids</span>
    <span class="meta-value">${bids}</span>
    <span class="meta-label">Date</span>
    <span class="meta-value">${ended}</span>
    <span class="meta-label">Sold signals</span>
    <span class="meta-value">${soldText}</span>
  `;
  document.getElementById('meta-grid').innerHTML = meta;

  // Pre-fill edit form
  setField('f-brand',       data.extracted_brand || 'Unknown');
  setField('f-line',        data.extracted_line === 'Unknown' ? '' : (data.extracted_line || ''));
  setField('f-condition',   data.extracted_condition || 'Unknown');
  setField('f-price',       data.price_jpy || '');
  setField('f-final-price', data.final_price_jpy || data.price_jpy || '');
  setField('f-count',       data.item_count || '1');
  setField('f-notes',       '');

  // Show/hide final-price field
  document.getElementById('final-price-field').style.display = 'block';

  // Mark fields that were auto-extracted with low confidence
  if (conf === 'low') {
    document.getElementById('f-brand').classList.add('modified');
    document.getElementById('f-line').classList.add('modified');
  } else {
    document.getElementById('f-brand').classList.remove('modified');
    document.getElementById('f-line').classList.remove('modified');
  }

  // Decision banner (if row was already reviewed)
  const banner = document.getElementById('decision-banner');
  if (data.decision) {
    banner.style.display = 'block';
    banner.className = `decision-banner banner-${data.decision}`;
    banner.textContent = data.decision === 'keep' ? '✓ Previously kept'
                       : data.decision === 'discard' ? '✗ Previously discarded'
                       : '↷ Previously skipped';
  } else {
    banner.style.display = 'none';
  }

  updateNavButtons();
  document.getElementById('f-brand').focus();
}

function setField(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.tagName === 'SELECT') {
    const opt = Array.from(el.options).find(o => o.value === value);
    el.value = opt ? value : el.options[0].value;
  } else {
    el.value = value;
  }
}

// ── Decisions ────────────────────────────────────────────────────────────────

async function decide(decision) {
  if (currentIdx === null) return;

  const body = {
    idx:            currentIdx,
    decision,
    brand:          document.getElementById('f-brand').value,
    line:           document.getElementById('f-line').value.trim(),
    condition:      document.getElementById('f-condition').value,
    price_jpy:      document.getElementById('f-price').value,
    final_price_jpy:document.getElementById('f-final-price').value,
    item_count:     document.getElementById('f-count').value,
    notes:          document.getElementById('f-notes').value.trim(),
  };

  const result = await fetch('/api/decide', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json());

  updateStats(result.stats);

  if (result.next_idx !== null && result.next_idx !== undefined) {
    await loadRow(result.next_idx);
  } else {
    showDone();
  }
}

// ── Navigation ───────────────────────────────────────────────────────────────

async function navigate(direction) {
  if (direction === -1) {
    if (historyPos > 0) {
      historyPos--;
      await loadRow(history[historyPos]);
    }
  } else {
    if (historyPos < history.length - 1) {
      historyPos++;
      await loadRow(history[historyPos]);
    } else {
      // Go to next pending after current
      const data = await fetch(`/api/next?after=${currentIdx}`).then(r => r.json());
      if (!data.done && data.idx !== null) renderRow(data);
    }
  }
}

function updateNavButtons() {
  document.getElementById('btn-prev').disabled = historyPos <= 0;
}

// ── Stats & UI helpers ───────────────────────────────────────────────────────

function updateStats(s) {
  stats = s;
  document.getElementById('s-kept').textContent = `✓ ${s.kept}`;
  document.getElementById('s-disc').textContent = `✗ ${s.discarded}`;
  document.getElementById('s-skip').textContent = `↷ ${s.skipped}`;

  const pct = s.total > 0 ? (s.reviewed / s.total * 100) : 0;
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-text').textContent =
    `${s.reviewed} / ${s.total} reviewed · ${s.remaining} remaining`;
}

function showDone() {
  document.getElementById('main').style.display = 'none';
  document.getElementById('actions').style.display = 'none';
  document.getElementById('done-screen').style.display = 'flex';
  document.getElementById('done-stats').textContent =
    `${stats.kept} kept · ${stats.discarded} discarded · ${stats.skipped} skipped`;
}

// ── Keyboard shortcuts ───────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  // Don't fire shortcuts while typing in a text/textarea field
  const tag = document.activeElement?.tagName;
  const isTyping = tag === 'TEXTAREA' || (tag === 'INPUT' && document.activeElement.type === 'text');
  if (isTyping && e.key !== 'Escape') return;

  if (e.key === 'Enter' || e.key === 'k' || e.key === 'K') { e.preventDefault(); decide('keep'); }
  else if (e.key === 'd' || e.key === 'D') { e.preventDefault(); decide('discard'); }
  else if (e.key === 's' || e.key === 'S') { e.preventDefault(); decide('skip'); }
  else if (e.key === 'ArrowLeft')          { e.preventDefault(); navigate(-1); }
  else if (e.key === 'ArrowRight')         { e.preventDefault(); navigate(1); }
  else if (e.key === 'Escape') { document.getElementById('f-brand').focus(); }
});

// ── Mark fields as modified when user edits them ─────────────────────────────
['f-brand', 'f-line', 'f-condition', 'f-price', 'f-final-price', 'f-count'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', () => el.classList.add('modified'));
});

init();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_latest_csv() -> Path | None:
    candidates = sorted(REVIEW_DIR.glob("scraped_sales_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main():
    parser = argparse.ArgumentParser(description="Interactive reviewer for scraped pen sale data")
    parser.add_argument("--input", type=str, default="", help="Path to scraped CSV (default: latest in data/labeled/review/)")
    parser.add_argument("--port", type=int, default=8787, help="Port to serve on (default: 8787)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    global state

    if args.input:
        csv_path = Path(args.input)
    else:
        csv_path = find_latest_csv()
        if csv_path is None:
            print("No scraped CSV found in data/labeled/review/. Run scrape_training_data.py first.")
            sys.exit(1)

    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
    state = ReviewState(csv_path)

    s = state.stats()
    print(f"\n{'─'*50}")
    print(f"  Input:     {csv_path.name}")
    print(f"  Total:     {s['total']} rows")
    print(f"  Reviewed:  {s['reviewed']}  (kept: {s['kept']}, discarded: {s['discarded']}, skipped: {s['skipped']})")
    print(f"  Remaining: {s['remaining']}")
    print(f"  Output:    {CHECKED_JSONL}")
    print(f"{'─'*50}")
    print(f"\n  Open:  http://localhost:{args.port}")
    print(f"  Stop:  Ctrl+C\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
