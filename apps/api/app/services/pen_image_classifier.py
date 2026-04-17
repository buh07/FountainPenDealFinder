"""
Pen image classifier service.

Given an image URL (e.g., from a Yahoo Auctions listing), downloads the image,
extracts features, and runs the trained sklearn model to identify brand/line.
Falls back to Claude Vision when local confidence is below threshold.

Usage:
    from apps.api.app.services.pen_image_classifier import classify_image_url
    result = classify_image_url("https://...")
    # → {"brand": "Pilot", "line": "Custom 742", "confidence": "high", "source": "local"}
"""

import base64
import io
import json
import os
import pickle
from pathlib import Path
from typing import TypedDict

import httpx
import numpy as np
from PIL import Image, ImageFilter

ROOT       = Path(__file__).resolve().parent.parent.parent.parent.parent
MODEL_PATH = ROOT / "models" / "visual" / "pen_classifier.pkl"
META_PATH  = ROOT / "models" / "visual" / "pen_classifier_meta.json"

LOCAL_CONFIDENCE_THRESHOLD = 0.60   # below this → fall back to Claude

_model_cache: dict = {}


class ClassifyResult(TypedDict):
    brand: str
    line: str
    condition: str
    confidence: str   # "high" | "medium" | "low"
    source: str       # "local" | "claude" | "error"
    notes: str


# ---------------------------------------------------------------------------
# Feature extraction (must match train_pen_classifier.py)
# ---------------------------------------------------------------------------

N_BINS = 8


def _extract_features(img: Image.Image) -> np.ndarray:
    img = img.resize((128, 128)).convert("RGB")
    hist_feats: list[float] = []
    for ch in img.split():
        hist, _ = np.histogram(np.array(ch), bins=N_BINS, range=(0, 256))
        hist = hist.astype(float) / (128 * 128)
        hist_feats.extend(hist.tolist())
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    edge_arr = np.array(edges, dtype=float) / 255.0
    edge_density = float(edge_arr.mean())
    edge_std     = float(edge_arr.std())
    w, h = img.size
    aspect = w / h
    return np.array(hist_feats + [edge_density, edge_std, aspect], dtype=float)


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _load_model() -> dict | None:
    if "model" in _model_cache:
        return _model_cache
    if not MODEL_PATH.exists():
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            obj = pickle.load(f)
        meta = {}
        if META_PATH.exists():
            with open(META_PATH, encoding="utf-8") as f:
                meta = json.load(f)
        _model_cache["model"] = obj
        _model_cache["meta"]  = meta
        return _model_cache
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Local classification
# ---------------------------------------------------------------------------

def _classify_local(img: Image.Image) -> ClassifyResult | None:
    cache = _load_model()
    if cache is None:
        return None
    pipe = cache["model"]["pipeline"]
    le   = cache["model"]["label_encoder"]
    feats = _extract_features(img).reshape(1, -1)
    proba = pipe.predict_proba(feats)[0]
    top_idx = int(np.argmax(proba))
    top_prob = float(proba[top_idx])
    label = le.inverse_transform([top_idx])[0]
    parts = label.split("|", 1)
    brand = parts[0] if len(parts) == 2 else label
    line  = parts[1] if len(parts) == 2 else "Unknown"
    if top_prob < LOCAL_CONFIDENCE_THRESHOLD:
        return None
    confidence = "high" if top_prob >= 0.85 else "medium"
    return ClassifyResult(
        brand=brand, line=line, condition="", confidence=confidence,
        source="local", notes=f"p={top_prob:.2f}"
    )


# ---------------------------------------------------------------------------
# Claude Vision fallback
# ---------------------------------------------------------------------------

_CLAUDE_SYSTEM = (
    "You are an expert fountain pen identifier. Identify brand, line/model, "
    "and condition from the image. Reply ONLY with JSON (no markdown): "
    '{"brand":"...","line":"...","condition":"...","confidence":"high|medium|low","notes":"..."}'
)


def _classify_claude(img: Image.Image) -> ClassifyResult:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ClassifyResult(brand="Unknown", line="Unknown", condition="",
                              confidence="low", source="error",
                              notes="ANTHROPIC_API_KEY not set")
    try:
        import anthropic
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        b64 = base64.standard_b64encode(buf.getvalue()).decode()
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=200,
            system=_CLAUDE_SYSTEM,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": "Identify this fountain pen."},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        r = json.loads(raw)
        return ClassifyResult(
            brand=r.get("brand", "Unknown"),
            line=r.get("line", "Unknown"),
            condition=r.get("condition", ""),
            confidence=r.get("confidence", "low"),
            source="claude",
            notes=r.get("notes", ""),
        )
    except Exception as e:
        return ClassifyResult(brand="Unknown", line="Unknown", condition="",
                              confidence="low", source="error", notes=str(e)[:120])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def classify_image_url(url: str, force_claude: bool = False) -> ClassifyResult:
    """Download image from URL and identify the pen."""
    try:
        resp = httpx.get(url, headers=_DOWNLOAD_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        return ClassifyResult(brand="Unknown", line="Unknown", condition="",
                              confidence="low", source="error", notes=str(e)[:120])

    if not force_claude:
        local = _classify_local(img)
        if local is not None:
            return local

    return _classify_claude(img)


def classify_image_bytes(data: bytes, force_claude: bool = False) -> ClassifyResult:
    """Classify from raw image bytes (already downloaded)."""
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        return ClassifyResult(brand="Unknown", line="Unknown", condition="",
                              confidence="low", source="error", notes=str(e)[:120])
    if not force_claude:
        local = _classify_local(img)
        if local is not None:
            return local
    return _classify_claude(img)
