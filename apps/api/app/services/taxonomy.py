from __future__ import annotations

import csv
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import get_settings

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None
    logger.warning("fcntl is unavailable; taxonomy feedback JSONL appends are not process-safe on this platform")


CANONICAL_CONDITIONS = ["A", "B+", "B", "C", "Parts/Repair"]


DEFAULT_BRAND_CATEGORIES = {
    "Namiki": "japanese_premium",
    "Nakaya": "japanese_premium",
    "Pilot": "japanese_core",
    "Sailor": "japanese_core",
    "Platinum": "japanese_core",
    "Pelikan": "european_luxury",
    "Montblanc": "european_luxury",
    "Unknown": "other",
}


DEFAULT_BRAND_ALIASES = {
    "Namiki": ["namiki", "ナミキ"],
    "Nakaya": ["nakaya", "中屋"],
    "Pilot": ["pilot", "パイロット"],
    "Sailor": ["sailor", "セーラー"],
    "Platinum": ["platinum", "プラチナ"],
    "Pelikan": ["pelikan"],
    "Montblanc": ["montblanc", "モンブラン"],
    "Unknown": ["unknown"],
}


DEFAULT_LINE_ALIASES = {
    "Pilot": {
        "Custom 743": ["custom 743", "743", "カスタム743"],
        "Custom 823": ["custom 823", "823", "カスタム823"],
    },
    "Sailor": {
        "1911 Large": ["1911 large", "1911l", "1911"],
    },
    "Platinum": {
        "3776 Century": ["3776 century", "3776", "century", "センチュリー"],
    },
    "Namiki": {
        "Yukari": ["yukari", "雪割", "蒔絵"],
    },
    "Pelikan": {
        "M800": ["m800"],
    },
    "Montblanc": {
        "146": ["146", "meisterstuck 146", "meisterstück 146"],
    },
}


CONDITION_ALIAS_MAP = {
    "a": "A",
    "mint": "A",
    "new": "A",
    "unused": "A",
    "n": "A",
    "b+": "B+",
    "bplus": "B+",
    "excellent": "B+",
    "like new": "B+",
    "very good": "B+",
    "美品": "B+",
    "b": "B",
    "good": "B",
    "used": "B",
    "c": "C",
    "fair": "C",
    "rough": "C",
    "d": "Parts/Repair",
    "parts/repair": "Parts/Repair",
    "parts": "Parts/Repair",
    "repair": "Parts/Repair",
    "junk": "Parts/Repair",
    "ジャンク": "Parts/Repair",
}


@dataclass
class TaxonomyType:
    brand: str
    line: str
    category: str
    aliases: list[str]


@dataclass
class TaxonomyCatalog:
    brand_alias_to_brand: dict[str, str]
    line_alias_to_type: list[tuple[str, str, str]]
    line_aliases_by_brand: dict[str, dict[str, set[str]]]
    brand_category: dict[str, str]


_CATALOG_CACHE: dict[str, Any] = {
    "seed_path": None,
    "feedback_path": None,
    "seed_state": None,
    "feedback_state": None,
    "catalog": None,
}
_CATALOG_LOCK = threading.RLock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _normalize_token(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return normalized.strip("_") or "unknown_fountain_pen"


def _clean_line_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value.replace("_", " ")).strip()
    return cleaned or None


def _taxonomy_paths() -> tuple[Path, Path]:
    settings = get_settings()
    root = _repo_root()

    seed_path = Path(settings.taxonomy_seed_path)
    if not seed_path.is_absolute():
        seed_path = root / seed_path

    feedback_path = Path(settings.taxonomy_feedback_types_path)
    if not feedback_path.is_absolute():
        feedback_path = root / feedback_path

    return seed_path, feedback_path


def _read_seed_rows(seed_path: Path) -> list[dict[str, str]]:
    if not seed_path.exists():
        return []

    with seed_path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_feedback_rows(feedback_path: Path) -> list[dict[str, Any]]:
    if not feedback_path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in feedback_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _file_state(path: Path) -> tuple[int, int]:
    if not path.exists():
        return (-1, -1)
    stat = path.stat()
    return (int(stat.st_mtime_ns), int(stat.st_size))


def invalidate_taxonomy_cache() -> None:
    with _CATALOG_LOCK:
        _CATALOG_CACHE["catalog"] = None


def _build_catalog(seed_path: Path, feedback_path: Path) -> TaxonomyCatalog:
    brand_alias_to_brand: dict[str, str] = {}
    line_aliases_by_brand: dict[str, dict[str, set[str]]] = {}

    for canonical_brand, aliases in DEFAULT_BRAND_ALIASES.items():
        for alias in aliases + [canonical_brand]:
            normalized = _normalize_token(alias)
            if normalized:
                brand_alias_to_brand[normalized] = canonical_brand

    def _register_line(brand: str, line: str, aliases: list[str] | set[str]) -> None:
        line_map = line_aliases_by_brand.setdefault(brand, {})
        alias_set = line_map.setdefault(line, set())
        alias_set.add(_normalize_token(line))
        for alias in aliases:
            normalized = _normalize_token(str(alias or ""))
            if normalized:
                alias_set.add(normalized)

    for brand, line_map in DEFAULT_LINE_ALIASES.items():
        for line, aliases in line_map.items():
            _register_line(brand, line, aliases)

    for row in _read_seed_rows(seed_path):
        brand = canonicalize_brand(row.get("brand"), brand_alias_to_brand=brand_alias_to_brand)
        line = _clean_line_label(row.get("line"))
        if not line:
            continue

        aliases: list[str] = []
        model_alias = row.get("model_alias")
        if model_alias:
            aliases.append(str(model_alias))
        _register_line(brand, line, aliases)

    for row in _read_feedback_rows(feedback_path):
        brand = canonicalize_brand(row.get("brand"), brand_alias_to_brand=brand_alias_to_brand)
        line = _clean_line_label(str(row.get("line") or ""))
        if not line:
            continue

        aliases_raw = row.get("aliases")
        aliases: list[str] = []
        if isinstance(aliases_raw, list):
            aliases = [str(alias) for alias in aliases_raw if str(alias or "").strip()]
        _register_line(brand, line, aliases)

    line_alias_to_type: list[tuple[str, str, str]] = []
    for brand, line_map in line_aliases_by_brand.items():
        for line, aliases in line_map.items():
            for alias in aliases:
                line_alias_to_type.append((alias, brand, line))

    line_alias_to_type.sort(key=lambda item: len(item[0]), reverse=True)

    brand_category = dict(DEFAULT_BRAND_CATEGORIES)
    for brand in line_aliases_by_brand:
        brand_category.setdefault(brand, "other")

    return TaxonomyCatalog(
        brand_alias_to_brand=brand_alias_to_brand,
        line_alias_to_type=line_alias_to_type,
        line_aliases_by_brand=line_aliases_by_brand,
        brand_category=brand_category,
    )


def load_taxonomy_catalog() -> TaxonomyCatalog:
    with _CATALOG_LOCK:
        seed_path, feedback_path = _taxonomy_paths()

        seed_state = _file_state(seed_path)
        feedback_state = _file_state(feedback_path)

        cached = _CATALOG_CACHE.get("catalog")
        if (
            cached is not None
            and _CATALOG_CACHE.get("seed_path") == str(seed_path)
            and _CATALOG_CACHE.get("feedback_path") == str(feedback_path)
            and _CATALOG_CACHE.get("seed_state") == seed_state
            and _CATALOG_CACHE.get("feedback_state") == feedback_state
        ):
            return cached

        catalog = _build_catalog(seed_path, feedback_path)
        _CATALOG_CACHE.update(
            {
                "seed_path": str(seed_path),
                "feedback_path": str(feedback_path),
                "seed_state": seed_state,
                "feedback_state": feedback_state,
                "catalog": catalog,
            }
        )
        return catalog


def _append_jsonl_row_locked(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def canonicalize_brand(
    value: Any,
    *,
    brand_alias_to_brand: dict[str, str] | None = None,
) -> str:
    if brand_alias_to_brand is None:
        brand_alias_to_brand = load_taxonomy_catalog().brand_alias_to_brand

    raw = str(value or "").strip()
    if not raw:
        return "Unknown"

    normalized = _normalize_token(raw)
    canonical = brand_alias_to_brand.get(normalized)
    if canonical:
        return canonical

    return raw.title()


def category_for_brand(brand: str) -> str:
    catalog = load_taxonomy_catalog()
    return catalog.brand_category.get(brand, "other")


def canonicalize_line(brand: str, line: Any) -> str | None:
    cleaned = _clean_line_label(str(line or ""))
    if not cleaned:
        return None

    catalog = load_taxonomy_catalog()
    normalized = _normalize_token(cleaned)

    line_map = catalog.line_aliases_by_brand.get(brand, {})
    for canonical_line, aliases in line_map.items():
        if normalized in aliases:
            return canonical_line

    for alias, alias_brand, alias_line in catalog.line_alias_to_type:
        if normalized == alias and alias_brand == brand:
            return alias_line

    return cleaned


def infer_brand_line_from_text(text: str) -> tuple[str, str | None]:
    catalog = load_taxonomy_catalog()
    normalized_text = _normalize_token(text)
    if not normalized_text:
        return "Unknown", None

    for alias, brand, line in catalog.line_alias_to_type:
        if alias and alias in normalized_text:
            return brand, line

    sorted_brand_aliases = sorted(
        catalog.brand_alias_to_brand.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, brand in sorted_brand_aliases:
        if alias and alias in normalized_text:
            return brand, None

    return "Unknown", None


def canonicalize_condition_grade(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "B"

    if raw in CANONICAL_CONDITIONS:
        return raw

    normalized = _normalize_token(raw).replace(" ", "")
    if normalized in CONDITION_ALIAS_MAP:
        return CONDITION_ALIAS_MAP[normalized]

    normalized_spaced = _normalize_token(raw)
    if normalized_spaced in CONDITION_ALIAS_MAP:
        return CONDITION_ALIAS_MAP[normalized_spaced]

    lowered = normalized_spaced
    if any(token in lowered for token in ["repair", "parts", "junk", "ジャンク"]):
        return "Parts/Repair"
    if any(token in lowered for token in ["mint", "new", "unused", "新品"]):
        return "A"
    if any(token in lowered for token in ["excellent", "like new", "美品"]):
        return "B+"
    if any(token in lowered for token in ["fair", "rough", "damage", "傷"]):
        return "C"

    return "B"


def classification_id_for(brand: str, line: str | None) -> str:
    return _normalize_identifier(f"{brand}_{line or 'fountain_pen'}")


def resolve_taxonomy(
    *,
    brand: Any = None,
    line: Any = None,
    classification_id: str | None = None,
    text: str | None = None,
) -> dict[str, str | None]:
    inferred_brand, inferred_line = infer_brand_line_from_text(
        " ".join(
            part for part in [str(text or ""), str(classification_id or "").replace("_", " ")] if part
        )
    )

    canonical_brand = canonicalize_brand(brand or inferred_brand)
    candidate_line = line if line not in (None, "") else inferred_line
    canonical_line = canonicalize_line(canonical_brand, candidate_line)

    return {
        "brand": canonical_brand,
        "line": canonical_line,
        "category": category_for_brand(canonical_brand),
        "classification_id": classification_id_for(canonical_brand, canonical_line),
    }


def add_taxonomy_feedback_type(
    *,
    brand: str,
    line: str,
    aliases: list[str],
    source_review_id: str,
    reviewer: str,
) -> None:
    if not brand or not line or not aliases:
        return

    _, feedback_path = _taxonomy_paths()
    feedback_path.parent.mkdir(parents=True, exist_ok=True)

    cleaned_aliases = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
    if not cleaned_aliases:
        return

    payload = {
        "brand": brand,
        "line": line,
        "aliases": cleaned_aliases,
        "source_review_id": source_review_id,
        "reviewer": reviewer,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _append_jsonl_row_locked(feedback_path, payload)

    invalidate_taxonomy_cache()


def taxonomy_standard() -> dict[str, Any]:
    catalog = load_taxonomy_catalog()
    types: list[TaxonomyType] = []
    for brand in sorted(catalog.line_aliases_by_brand.keys()):
        line_map = catalog.line_aliases_by_brand[brand]
        for line in sorted(line_map.keys()):
            aliases = sorted(alias for alias in line_map[line] if alias != _normalize_token(line))
            types.append(
                TaxonomyType(
                    brand=brand,
                    line=line,
                    category=category_for_brand(brand),
                    aliases=aliases,
                )
            )

    categories: dict[str, list[str]] = {}
    for brand, category in sorted(catalog.brand_category.items()):
        categories.setdefault(category, []).append(brand)

    return {
        "categories": categories,
        "conditions": list(CANONICAL_CONDITIONS),
        "types": [
            {
                "brand": entry.brand,
                "line": entry.line,
                "category": entry.category,
                "aliases": entry.aliases,
            }
            for entry in types
        ],
    }
