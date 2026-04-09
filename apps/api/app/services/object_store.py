from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import ListingAsset, RawListing


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _store_root() -> Path:
    settings = get_settings()
    root = Path(settings.object_store_root)
    if root.is_absolute():
        return root
    return _repo_root() / root


def _to_relative(path: Path) -> str:
    root = _repo_root()
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


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


def _asset_exists(session: Session, listing_id: str, asset_type: str, content_hash: str) -> bool:
    existing = session.scalar(
        select(ListingAsset.asset_id).where(
            ListingAsset.listing_id == listing_id,
            ListingAsset.asset_type == asset_type,
            ListingAsset.content_hash == content_hash,
        )
    )
    return existing is not None


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_suffix(source_url: str | None, default: str) -> str:
    if not source_url:
        return default
    parsed = urlparse(source_url)
    suffix = Path(parsed.path).suffix.strip().lower()
    if suffix and len(suffix) <= 10:
        return suffix
    return default


def _persist_asset(
    session: Session,
    listing: RawListing,
    *,
    asset_type: str,
    source_url: str | None,
    content: bytes,
    suffix: str,
) -> bool:
    content_hash = _hash_bytes(content)
    if _asset_exists(session, listing.listing_id, asset_type, content_hash):
        return False

    root = _store_root()
    asset_dir = root / listing.source / listing.listing_id / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    file_path = asset_dir / f"{content_hash}{suffix}"
    if not file_path.exists():
        file_path.write_bytes(content)

    session.add(
        ListingAsset(
            listing_id=listing.listing_id,
            asset_type=asset_type,
            local_path=_to_relative(file_path),
            source_url=source_url,
            content_hash=content_hash,
        )
    )
    return True


def _capture_image_assets(session: Session, listing: RawListing) -> int:
    image_urls = _decode_images(listing.images_json)
    if not image_urls:
        return 0

    created = 0
    timeout = httpx.Timeout(12.0, connect=6.0)
    settings = get_settings()
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for image_url in image_urls:
            try:
                response = client.get(image_url)
                response.raise_for_status()
            except httpx.HTTPError:
                continue

            persisted_image = _persist_asset(
                session,
                listing,
                asset_type="image",
                source_url=image_url,
                content=response.content,
                suffix=_safe_suffix(image_url, ".img"),
            )
            if persisted_image:
                created += 1

            if settings.object_store_generate_thumbnails:
                thumbnail = _build_thumbnail_bytes(
                    response.content,
                    max_px=max(32, int(settings.object_store_thumbnail_max_px)),
                )
                if thumbnail is not None:
                    thumbnail_bytes, thumbnail_suffix = thumbnail
                    if _persist_asset(
                        session,
                        listing,
                        asset_type="thumbnail",
                        source_url=image_url,
                        content=thumbnail_bytes,
                        suffix=thumbnail_suffix,
                    ):
                        created += 1

    return created


def _capture_page_asset(
    session: Session,
    listing: RawListing,
    source_payload: dict | None,
) -> int:
    html_text = ""
    if isinstance(source_payload, dict):
        html_text = str(source_payload.get("raw_html") or "").strip()
        if not html_text:
            raw_attributes = source_payload.get("raw_attributes")
            if isinstance(raw_attributes, dict):
                html_text = str(raw_attributes.get("raw_html") or raw_attributes.get("page_html") or "").strip()

    if not html_text:
        html_text = str(listing.description_raw or "").strip()

    if not html_text:
        return 0

    content = html_text.encode("utf-8")
    created = _persist_asset(
        session,
        listing,
        asset_type="page_capture",
        source_url=listing.url,
        content=content,
        suffix=".html",
    )
    return 1 if created else 0


def _build_thumbnail_bytes(content: bytes, max_px: int) -> tuple[bytes, str] | None:
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.thumbnail((max_px, max_px))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue(), ".jpg"
    except Exception:
        return None


def should_capture_listing_assets(listing: RawListing, deal_bucket: str) -> bool:
    settings = get_settings()
    if not settings.object_store_enable_capture:
        return False

    policy = settings.object_store_capture_policy
    if policy == "none":
        return False
    if policy == "all":
        return True

    is_scored = deal_bucket in {"confident", "potential"}
    ends_at = listing.ends_at
    ending_soon = False
    if listing.listing_format == "auction" and ends_at is not None:
        target = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        ending_soon = now <= target.astimezone(timezone.utc) <= now + timedelta(hours=max(1, settings.worker_priority_window_hours))

    if policy == "scored_only":
        return is_scored
    if policy == "ending_soon_only":
        return ending_soon
    return is_scored or ending_soon


def capture_listing_assets(
    session: Session,
    listing: RawListing,
    *,
    deal_bucket: str,
    source_payload: dict | None = None,
) -> int:
    if not should_capture_listing_assets(listing, deal_bucket):
        return 0

    created = 0
    created += _capture_page_asset(session, listing, source_payload)
    created += _capture_image_assets(session, listing)

    if created > 0:
        session.flush()
    return created
