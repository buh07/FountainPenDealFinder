from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from ..core.config import get_settings
from ..models import RawListing
from .taxonomy import canonicalize_condition_grade, classification_id_for, resolve_taxonomy, taxonomy_standard


CONDITION_KEYWORDS = [
    ("傷", "micro_scratches"),
    ("スレ", "micro_scratches"),
    ("scratch", "micro_scratches"),
    ("凹", "dent_or_ding"),
    ("dent", "dent_or_ding"),
    ("メッキ", "plating_wear"),
    ("錆", "trim_wear"),
    ("曲が", "bent_nib_possible"),
    ("割れ", "hairline_crack"),
    ("ヒビ", "hairline_crack"),
    ("ジャンク", "parts_repair"),
    ("repair", "parts_repair"),
    ("名入れ", "name_engraving"),
    ("engraving", "name_engraving"),
    ("漆", "urushi_finish"),
]


def _normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return normalized.strip("_") or "unknown_fountain_pen"


def _decode_images(images_json: str | None) -> list[str]:
    if not images_json:
        return []
    try:
        payload = json.loads(images_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item).strip() for item in payload if str(item).strip()]


def _estimate_item_count(text: str, lot_size_hint: int) -> int:
    lower = text.lower()
    match = re.search(r"(\d+)\s*(?:本|pen|pens)", lower)
    if match:
        return max(1, int(match.group(1)))

    if "まとめ" in text or "セット" in text or re.search(r"\blot\b", lower):
        return max(2, lot_size_hint)

    return max(1, lot_size_hint)


def _extract_condition_flags(text: str) -> list[str]:
    lower = text.lower()
    flags = [flag for keyword, flag in CONDITION_KEYWORDS if keyword in lower]
    deduped: list[str] = []
    for flag in flags:
        if flag not in deduped:
            deduped.append(flag)
    return deduped


def _text_blob(listing: RawListing) -> str:
    return " ".join(
        part
        for part in [listing.title, listing.description_raw or "", listing.condition_text or ""]
        if part
    )


def _tokenize(value: str) -> list[str]:
    lowered = value.lower().replace("+", " plus ")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    tokens = [token.strip() for token in lowered.split() if token.strip()]
    return tokens


def _image_tokens(image_urls: list[str]) -> list[str]:
    tokens: list[str] = []
    for image_url in image_urls:
        parsed = urlparse(image_url)
        parts = [parsed.path, parsed.query, parsed.fragment]
        for part in parts:
            tokens.extend(_tokenize(part))
    return tokens


@lru_cache(maxsize=1)
def _taxonomy_type_index() -> list[dict[str, Any]]:
    standard = taxonomy_standard()
    entries: list[dict[str, Any]] = []
    for entry in standard.get("types", []):
        if not isinstance(entry, dict):
            continue
        brand = str(entry.get("brand") or "Unknown")
        line = str(entry.get("line") or "fountain_pen")
        aliases = [str(alias) for alias in entry.get("aliases", []) if str(alias).strip()]

        tokens: list[str] = []
        for value in [brand, line, *aliases]:
            tokens.extend(_tokenize(value))

        filtered_tokens: list[str] = []
        for token in tokens:
            if token.isdigit() and len(token) >= 3:
                filtered_tokens.append(token)
            elif len(token) >= 4:
                filtered_tokens.append(token)

        entries.append(
            {
                "brand": brand,
                "line": line,
                "classification_id": classification_id_for(brand, line),
                "tokens": sorted(set(filtered_tokens)),
            }
        )
    return entries


def _stage1_text_candidates(text_blob: str) -> dict[str, Any]:
    taxonomy = resolve_taxonomy(text=text_blob)
    return {
        "brand": str(taxonomy.get("brand") or "Unknown"),
        "line": taxonomy.get("line"),
        "classification_id": str(taxonomy.get("classification_id") or "unknown_fountain_pen"),
        "taxonomy": taxonomy,
    }


def _stage2_image_embedding_inference(
    listing: RawListing,
    text_stage: dict[str, Any],
) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.image_classifier_enabled:
        return None

    image_urls = _decode_images(listing.images_json)
    if not image_urls:
        return None

    image_tokens = _image_tokens(image_urls)
    searchable = " ".join(image_tokens)
    text_brand = str(text_stage.get("brand") or "Unknown")

    best_entry: dict[str, Any] | None = None
    best_score = -999

    for entry in _taxonomy_type_index():
        score = 0
        matched_tokens: list[str] = []
        for token in entry.get("tokens", []):
            if token and token in searchable:
                score += 1
                matched_tokens.append(token)

        if text_brand != "Unknown" and entry["brand"] != text_brand:
            score -= 1

        if score > best_score:
            best_score = score
            best_entry = {
                **entry,
                "matched_tokens": sorted(set(matched_tokens)),
            }
        elif score == best_score and best_entry is not None:
            if (entry["brand"], entry["line"]) < (best_entry["brand"], best_entry["line"]):
                best_entry = {
                    **entry,
                    "matched_tokens": sorted(set(matched_tokens)),
                }

    if best_entry is None or best_score <= 0:
        return None

    confidence = min(0.9, 0.35 + (best_score * 0.12))
    return {
        "brand": best_entry["brand"],
        "line": best_entry["line"],
        "classification_id": best_entry["classification_id"],
        "confidence": round(confidence, 3),
        "evidence": {
            "model": settings.image_embedding_model_name,
            "matched_score": best_score,
            "matched_tokens": best_entry.get("matched_tokens", []),
            "images_used": min(5, len(image_urls)),
        },
    }


def _stage3_lot_decomposition(listing: RawListing, text_blob: str) -> dict[str, Any]:
    item_count = _estimate_item_count(text_blob, listing.lot_size_hint)
    lot_confidence = 0.9 if item_count == 1 else 0.62
    return {
        "item_count_estimate": item_count,
        "lot_decomposition_confidence": round(lot_confidence, 3),
    }


def _stage4_taxonomy_resolution(
    text_blob: str,
    text_stage: dict[str, Any],
    image_stage: dict[str, Any] | None,
) -> dict[str, Any]:
    if image_stage is None:
        return text_stage

    # Blend text and image evidence by appending image hint into taxonomy resolution text.
    hinted = f"{text_blob} {image_stage['brand']} {image_stage['line']}"
    taxonomy = resolve_taxonomy(text=hinted)

    if str(taxonomy.get("brand") or "Unknown") == "Unknown" and image_stage["confidence"] >= 0.6:
        return {
            "brand": image_stage["brand"],
            "line": image_stage["line"],
            "classification_id": image_stage["classification_id"],
            "taxonomy": {
                "brand": image_stage["brand"],
                "line": image_stage["line"],
                "classification_id": image_stage["classification_id"],
            },
        }

    return {
        "brand": str(taxonomy.get("brand") or text_stage["brand"]),
        "line": taxonomy.get("line") or text_stage.get("line"),
        "classification_id": str(
            taxonomy.get("classification_id")
            or text_stage.get("classification_id")
            or _normalize_identifier(f"{text_stage['brand']}_{text_stage.get('line') or 'fountain_pen'}")
        ),
        "taxonomy": taxonomy,
    }


def _stage5_condition_resolution(text_blob: str) -> dict[str, Any]:
    condition_flags = _extract_condition_flags(text_blob)
    if "parts_repair" in condition_flags:
        condition_grade = "Parts/Repair"
    elif any(flag in condition_flags for flag in ["hairline_crack", "bent_nib_possible"]):
        condition_grade = "C"
    elif any(token in text_blob for token in ["目立った傷や汚れなし", "美品", "good condition"]):
        condition_grade = "B+"
    else:
        condition_grade = "B"

    condition_grade = canonicalize_condition_grade(condition_grade)
    condition_confidence = 0.76 if condition_flags else 0.5

    return {
        "condition_grade": condition_grade,
        "condition_flags": condition_flags,
        "condition_confidence": round(condition_confidence, 3),
    }


def _stage6_uncertainty_and_explanation(
    *,
    taxonomy_stage: dict[str, Any],
    condition_stage: dict[str, Any],
    lot_stage: dict[str, Any],
    image_stage: dict[str, Any] | None,
    image_blend_applied: bool,
    classification_confidence: float,
) -> tuple[list[str], dict[str, Any]]:
    uncertainty_tags: list[str] = []
    if taxonomy_stage["brand"] == "Unknown":
        uncertainty_tags.append("taxonomy_unknown")
    if classification_confidence < 0.55:
        uncertainty_tags.append("low_classification_confidence")
    if lot_stage["item_count_estimate"] > 1:
        uncertainty_tags.append("lot_manual_review_recommended")
    if condition_stage["condition_grade"] in {"C", "Parts/Repair"}:
        uncertainty_tags.append("condition_risk_high")
    if image_stage is None:
        uncertainty_tags.append("image_evidence_unavailable")
    elif not image_blend_applied:
        uncertainty_tags.append("image_evidence_low_confidence")

    explanation = {
        "stage1_text": {
            "brand": taxonomy_stage["brand"],
            "line": taxonomy_stage["line"],
        },
        "stage2_image": image_stage,
        "stage3_lot": lot_stage,
        "stage5_condition": {
            "grade": condition_stage["condition_grade"],
            "flags": condition_stage["condition_flags"],
        },
        "uncertainty_tags": uncertainty_tags,
    }
    return uncertainty_tags, explanation


def classify_listing_multi_stage(listing: RawListing) -> dict[str, Any]:
    text_blob = _text_blob(listing)
    settings = get_settings()

    stage1 = _stage1_text_candidates(text_blob)
    stage2 = _stage2_image_embedding_inference(listing, stage1)
    stage3 = _stage3_lot_decomposition(listing, text_blob)
    stage4 = _stage4_taxonomy_resolution(text_blob, stage1, stage2)
    stage5 = _stage5_condition_resolution(text_blob)

    base_conf = 0.82 if stage4["brand"] != "Unknown" else 0.48
    lot_penalty = min(0.35, 0.05 * max(0, int(stage3["item_count_estimate"]) - 1))
    classification_confidence = max(0.35, min(0.97, base_conf - lot_penalty))
    image_blend_applied = False
    if stage2 is not None and float(stage2["confidence"]) >= settings.image_classifier_blend_min_confidence:
        classification_confidence = min(
            0.97,
            (classification_confidence * 0.75) + (float(stage2["confidence"]) * 0.25),
        )
        image_blend_applied = True

    uncertainty_tags, stage_explanations = _stage6_uncertainty_and_explanation(
        taxonomy_stage=stage4,
        condition_stage=stage5,
        lot_stage=stage3,
        image_stage=stage2,
        image_blend_applied=image_blend_applied,
        classification_confidence=classification_confidence,
    )

    classification_id = str(
        stage4.get("classification_id")
        or _normalize_identifier(f"{stage4['brand']}_{stage4.get('line') or 'fountain_pen'}")
    )

    items = []
    for idx in range(int(stage3["item_count_estimate"])):
        items.append(
            {
                "item_index": idx,
                "classification_id": classification_id,
                "condition_grade": stage5["condition_grade"],
                "condition_flags": stage5["condition_flags"],
                "visibility_confidence": max(
                    0.3,
                    float(stage3["lot_decomposition_confidence"]) - (idx * 0.03),
                ),
            }
        )

    return {
        "classification_id": classification_id,
        "brand": stage4["brand"],
        "line": stage4.get("line"),
        "nib_material": None,
        "nib_size": None,
        "condition_grade": stage5["condition_grade"],
        "condition_flags": stage5["condition_flags"],
        "item_count_estimate": int(stage3["item_count_estimate"]),
        "items": items,
        "classification_confidence": round(classification_confidence, 3),
        "condition_confidence": float(stage5["condition_confidence"]),
        "lot_decomposition_confidence": float(stage3["lot_decomposition_confidence"]),
        "text_evidence": text_blob[:600],
        "image_evidence": json.dumps(stage2, ensure_ascii=False) if stage2 is not None else None,
        "uncertainty_tags": uncertainty_tags,
        "stage_explanations": stage_explanations,
    }
