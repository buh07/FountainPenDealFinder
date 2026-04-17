#!/usr/bin/env python3
"""
Train a visual pen classifier from Claude-labeled images.

Features: color histogram (RGB 8-bin per channel = 512 floats) +
          texture (edge density via PIL ImageFilter) + aspect ratio.

Output:
    models/visual/pen_classifier.pkl     — sklearn Pipeline (scaler + SVC)
    models/visual/pen_classifier_meta.json — label map, class names, eval metrics

Usage:
    python3 scripts/train_pen_classifier.py
    python3 scripts/train_pen_classifier.py --min-samples 5
"""

import argparse
import json
import pickle
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.svm import SVC

ROOT          = Path(__file__).resolve().parent.parent
LABELED_PATH  = ROOT / "data" / "images" / "labeled_manifest.jsonl"
MODEL_DIR     = ROOT / "models" / "visual"
MODEL_PATH    = MODEL_DIR / "pen_classifier.pkl"
META_PATH     = MODEL_DIR / "pen_classifier_meta.json"

N_BINS = 8   # per channel for color histogram


def extract_features(img_path: Path) -> np.ndarray | None:
    try:
        img = Image.open(img_path).convert("RGB")
        img = img.resize((128, 128))

        # Color histogram: 8 bins × 3 channels = 24 floats (normalized)
        hist_feats = []
        for ch in img.split():
            hist, _ = np.histogram(np.array(ch), bins=N_BINS, range=(0, 256))
            hist = hist.astype(float) / (128 * 128)
            hist_feats.extend(hist.tolist())

        # Edge density (texture proxy)
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_arr = np.array(edges, dtype=float) / 255.0
        edge_density = float(edge_arr.mean())
        edge_std     = float(edge_arr.std())

        # Aspect ratio
        w, h = img.size
        aspect = w / h

        return np.array(hist_feats + [edge_density, edge_std, aspect], dtype=float)
    except Exception:
        return None


def load_labeled_manifest() -> list[dict]:
    if not LABELED_PATH.exists():
        return []
    rows = []
    with LABELED_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def make_label(r: dict) -> str:
    brand = r.get("claude_brand") or r.get("brand", "Unknown")
    line  = r.get("claude_line")  or r.get("line",  "Unknown")
    if brand == "Unknown" or line == "Unknown":
        return "Unknown"
    return f"{brand}|{line}"


def main():
    parser = argparse.ArgumentParser(description="Train pen image classifier")
    parser.add_argument("--min-samples", type=int, default=5,
                        help="Minimum labeled images needed per class (default: 5)")
    args = parser.parse_args()

    manifest = load_labeled_manifest()
    if not manifest:
        print(f"No labeled images found at {LABELED_PATH}")
        print("Run label_images_claude.py first.")
        return

    # Filter to high/medium confidence labels and non-Unknown classes
    usable = [r for r in manifest
              if r.get("claude_confidence") in ("high", "medium")
              and make_label(r) != "Unknown"]
    print(f"[train] Labeled manifest rows : {len(manifest)}")
    print(f"[train] High/medium confidence: {len(usable)}")

    label_counts = Counter(make_label(r) for r in usable)
    valid_classes = {cls for cls, cnt in label_counts.items() if cnt >= args.min_samples}
    print(f"[train] Classes with ≥{args.min_samples} samples: {len(valid_classes)}")

    if len(valid_classes) < 2:
        print(f"Need at least 2 classes with ≥{args.min_samples} samples each. "
              f"Collect more images or lower --min-samples.")
        return

    samples = [r for r in usable if make_label(r) in valid_classes]

    X_list, y_list = [], []
    skipped = 0
    for r in samples:
        img_path = ROOT / r["image_path"]
        feats = extract_features(img_path)
        if feats is None:
            skipped += 1
            continue
        X_list.append(feats)
        y_list.append(make_label(r))

    print(f"[train] Feature extraction: {len(X_list)} ok, {skipped} failed")

    if len(X_list) < 10:
        print("Too few samples for meaningful training. Need more labeled images.")
        return

    X = np.array(X_list)
    le = LabelEncoder()
    y = le.fit_transform(y_list)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    SVC(kernel="rbf", C=10, gamma="scale", probability=True)),
    ])

    # Cross-val report
    n_splits = min(5, Counter(y_list).most_common()[-1][1])
    if n_splits >= 2:
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        y_pred_cv = cross_val_predict(pipe, X, y, cv=cv)
        report = classification_report(y, y_pred_cv, target_names=le.classes_, output_dict=True)
        print("\nCross-validated classification report:")
        print(classification_report(y, y_pred_cv, target_names=le.classes_))
    else:
        report = {}
        print("[warn] Too few samples for cross-validation; skipping.")

    # Final model on all data
    pipe.fit(X, y)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"pipeline": pipe, "label_encoder": le}, f)

    meta = {
        "classes":       le.classes_.tolist(),
        "n_classes":     int(len(le.classes_)),
        "n_samples":     int(len(X_list)),
        "n_features":    int(X.shape[1]),
        "min_samples":   args.min_samples,
        "cv_report":     report,
        "label_counts":  {k: int(v) for k, v in label_counts.items() if k in valid_classes},
    }
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n[done] Model saved to {MODEL_PATH}")
    print(f"       Meta  saved to {META_PATH}")
    print(f"       Classes: {meta['n_classes']}  Samples: {meta['n_samples']}")


if __name__ == "__main__":
    main()
