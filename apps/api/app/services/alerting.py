import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..models import HealthAlertEvent
from ..schemas import HealthAlertDispatchResponse, HealthMetricsResponse


DEDUPE_ELIGIBLE_REASONS = {"sent", "request_failed", "non_2xx_response"}


def _normalize_alert_keys(alerts: list[str]) -> list[str]:
    deduped = sorted({str(alert).strip() for alert in alerts if str(alert).strip()})
    return deduped


def _alert_signature(alert_keys: list[str], window_hours: int) -> str | None:
    if not alert_keys:
        return None

    payload = {
        "alerts": alert_keys,
        "window_hours": max(1, int(window_hours)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_dispatch_event(session: Session, signature: str) -> HealthAlertEvent | None:
    return session.scalar(
        select(HealthAlertEvent)
        .where(
            HealthAlertEvent.alert_signature == signature,
            HealthAlertEvent.deduped.is_(False),
            HealthAlertEvent.reason.in_(DEDUPE_ELIGIBLE_REASONS),
        )
        .order_by(HealthAlertEvent.created_at.desc())
        .limit(1)
    )


def _persist_event(
    session: Session,
    metrics: HealthMetricsResponse,
    alert_keys: list[str],
    response: HealthAlertDispatchResponse,
) -> None:
    try:
        event = HealthAlertEvent(
            generated_at=metrics.generated_at,
            window_hours=max(1, metrics.window_hours),
            alert_signature=response.alert_signature,
            alert_keys_json=json.dumps(alert_keys, ensure_ascii=False),
            alert_count=response.alert_count,
            sent=response.sent,
            reason=response.reason,
            destination=response.destination,
            status_code=response.status_code,
            deduped=response.deduped,
            cooldown_remaining_seconds=response.cooldown_remaining_seconds,
        )
        session.add(event)
        session.commit()
    except Exception:
        session.rollback()


def _post_with_retry(
    destination: str,
    payload: dict[str, object],
    timeout_seconds: int,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> httpx.Response:
    attempts = max(1, retry_attempts)
    for attempt in range(attempts):
        try:
            response = httpx.post(
                destination,
                json=payload,
                timeout=max(1, timeout_seconds),
            )
            if response.status_code >= 500 and attempt < attempts - 1:
                sleep_seconds = max(0.0, retry_backoff_seconds) * (2**attempt)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                continue
            return response
        except httpx.HTTPError:
            if attempt >= attempts - 1:
                raise
            sleep_seconds = max(0.0, retry_backoff_seconds) * (2**attempt)
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
    raise RuntimeError("alert dispatch retry loop exited unexpectedly")


def dispatch_health_alerts(
    session: Session,
    metrics: HealthMetricsResponse,
) -> HealthAlertDispatchResponse:
    alert_keys = _normalize_alert_keys(metrics.alerts)
    alert_count = len(alert_keys)
    alert_signature = _alert_signature(alert_keys, metrics.window_hours)

    if alert_count == 0:
        response = HealthAlertDispatchResponse(
            sent=False,
            reason="no_alerts",
            alert_count=0,
            alert_signature=alert_signature,
        )
        _persist_event(session, metrics, alert_keys, response)
        return response

    settings = get_settings()
    destination = settings.monitoring_alert_webhook_url.strip()
    dedupe_window_seconds = max(0, settings.monitoring_alert_dedupe_window_seconds)

    if not destination:
        response = HealthAlertDispatchResponse(
            sent=False,
            reason="webhook_not_configured",
            alert_count=alert_count,
            alert_signature=alert_signature,
        )
        _persist_event(session, metrics, alert_keys, response)
        return response

    if alert_signature and dedupe_window_seconds > 0:
        last_event = _latest_dispatch_event(session, alert_signature)
        last_created_at = _as_utc(last_event.created_at if last_event else None)
        if last_created_at is not None:
            next_allowed_at = last_created_at + timedelta(seconds=dedupe_window_seconds)
            now = datetime.now(timezone.utc)
            if now < next_allowed_at:
                cooldown_remaining_seconds = max(1, int((next_allowed_at - now).total_seconds()))
                response = HealthAlertDispatchResponse(
                    sent=False,
                    reason="deduped_recent_alert",
                    alert_count=alert_count,
                    destination=destination,
                    deduped=True,
                    cooldown_remaining_seconds=cooldown_remaining_seconds,
                    alert_signature=alert_signature,
                )
                _persist_event(session, metrics, alert_keys, response)
                return response

    payload = {
        "type": "health_alert",
        "generated_at": metrics.generated_at.isoformat(),
        "window_hours": metrics.window_hours,
        "alerts": alert_keys,
        "source_counts": metrics.source_counts,
        "parse_completeness_avg": metrics.parse_completeness_avg,
        "non_discard_rate": metrics.non_discard_rate,
        "false_positive_rate": metrics.false_positive_rate,
        "baseline_eval_pass": metrics.baseline_eval_pass,
        "active_model_versions": metrics.active_model_versions,
        "model_age_hours": metrics.model_age_hours,
        "recent_non_stale_listing_count": metrics.recent_non_stale_listing_count,
        "latest_non_stale_listing_at": (
            metrics.latest_non_stale_listing_at.isoformat() if metrics.latest_non_stale_listing_at else None
        ),
        "listing_freshness_hours": metrics.listing_freshness_hours,
    }

    try:
        result = _post_with_retry(
            destination,
            payload=payload,
            timeout_seconds=settings.monitoring_alert_webhook_timeout_seconds,
            retry_attempts=settings.monitoring_alert_retry_attempts,
            retry_backoff_seconds=settings.monitoring_alert_retry_backoff_seconds,
        )
    except httpx.HTTPError:
        response = HealthAlertDispatchResponse(
            sent=False,
            reason="request_failed",
            alert_count=alert_count,
            destination=destination,
            alert_signature=alert_signature,
        )
        _persist_event(session, metrics, alert_keys, response)
        return response

    if 200 <= result.status_code < 300:
        response = HealthAlertDispatchResponse(
            sent=True,
            reason="sent",
            alert_count=alert_count,
            destination=destination,
            status_code=result.status_code,
            alert_signature=alert_signature,
        )
        _persist_event(session, metrics, alert_keys, response)
        return response

    response = HealthAlertDispatchResponse(
        sent=False,
        reason="non_2xx_response",
        alert_count=alert_count,
        destination=destination,
        status_code=result.status_code,
        alert_signature=alert_signature,
    )
    _persist_event(session, metrics, alert_keys, response)
    return response
