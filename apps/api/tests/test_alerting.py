from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.main import app
from app.models import HealthAlertEvent
from app.schemas import HealthMetricsResponse
from app.services import alerting


client = TestClient(app)


def _sample_metrics(alerts: list[str]) -> HealthMetricsResponse:
    return HealthMetricsResponse(
        generated_at=datetime.now(timezone.utc),
        window_hours=24,
        total_recent_listings=10,
        source_counts={"mercari": 4, "rakuma": 3, "yahoo_auctions": 2, "yahoo_flea_market": 1},
        parse_completeness_avg=0.8,
        non_discard_rate=0.3,
        manual_review_count=0,
        false_positive_rate=None,
        baseline_eval_pass=True,
        alerts=alerts,
    )


def _reset_alert_events() -> None:
    with SessionLocal() as session:
        session.execute(delete(HealthAlertEvent))
        session.commit()


def test_dispatch_health_alerts_returns_no_alerts_without_destinations():
    init_db()
    _reset_alert_events()

    with SessionLocal() as session:
        result = alerting.dispatch_health_alerts(session, _sample_metrics(alerts=[]))

        assert result.sent is False
        assert result.reason == "no_alerts"
        assert result.alert_count == 0

        rows = session.scalars(select(HealthAlertEvent)).all()
        assert len(rows) == 1
        assert rows[0].reason == "no_alerts"


def test_dispatch_health_alerts_returns_webhook_not_configured_for_alerts(monkeypatch):
    init_db()
    _reset_alert_events()

    class _Settings:
        monitoring_alert_webhook_url = ""
        monitoring_alert_webhook_timeout_seconds = 10
        monitoring_alert_dedupe_window_seconds = 3600
        monitoring_alert_retry_attempts = 3
        monitoring_alert_retry_backoff_seconds = 0.0

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())

    with SessionLocal() as session:
        result = alerting.dispatch_health_alerts(session, _sample_metrics(alerts=["parse_completeness_low"]))

        assert result.sent is False
        assert result.reason == "webhook_not_configured"
        assert result.alert_count == 1
        assert result.alert_signature is not None


def test_dispatch_health_alerts_posts_to_webhook(monkeypatch):
    init_db()
    _reset_alert_events()
    captured = {"url": None, "json": None, "timeout": None, "calls": 0}

    class _Settings:
        monitoring_alert_webhook_url = "https://example.test/hook"
        monitoring_alert_webhook_timeout_seconds = 5
        monitoring_alert_dedupe_window_seconds = 3600
        monitoring_alert_retry_attempts = 3
        monitoring_alert_retry_backoff_seconds = 0.0

    class _Response:
        status_code = 200

    def _fake_post(url, json, timeout):
        captured["calls"] += 1
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())
    monkeypatch.setattr(alerting.httpx, "post", _fake_post)

    with SessionLocal() as session:
        result = alerting.dispatch_health_alerts(session, _sample_metrics(alerts=["source_low_volume:rakuma"]))

        assert result.sent is True
        assert result.reason == "sent"
        assert result.destination == "https://example.test/hook"
        assert result.status_code == 200
        assert captured["calls"] == 1
        assert captured["url"] == "https://example.test/hook"
        assert captured["timeout"] == 5
        assert captured["json"]["type"] == "health_alert"

        events = session.scalars(select(HealthAlertEvent).where(HealthAlertEvent.reason == "sent")).all()
        assert len(events) == 1


def test_dispatch_health_alerts_dedupes_within_cooldown(monkeypatch):
    init_db()
    _reset_alert_events()
    captured = {"calls": 0}

    class _Settings:
        monitoring_alert_webhook_url = "https://example.test/hook"
        monitoring_alert_webhook_timeout_seconds = 5
        monitoring_alert_dedupe_window_seconds = 3600
        monitoring_alert_retry_attempts = 3
        monitoring_alert_retry_backoff_seconds = 0.0

    class _Response:
        status_code = 200

    def _fake_post(url, json, timeout):  # noqa: ARG001
        captured["calls"] += 1
        return _Response()

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())
    monkeypatch.setattr(alerting.httpx, "post", _fake_post)

    metrics = _sample_metrics(alerts=["source_low_volume:rakuma", "parse_completeness_low"])

    with SessionLocal() as session:
        first = alerting.dispatch_health_alerts(session, metrics)
        second = alerting.dispatch_health_alerts(session, metrics)

        assert first.reason == "sent"
        assert second.sent is False
        assert second.reason == "deduped_recent_alert"
        assert second.deduped is True
        assert second.cooldown_remaining_seconds is not None
        assert second.cooldown_remaining_seconds > 0
        assert captured["calls"] == 1

        deduped_rows = session.scalars(
            select(HealthAlertEvent).where(HealthAlertEvent.reason == "deduped_recent_alert")
        ).all()
        assert len(deduped_rows) == 1


def test_dispatch_health_alerts_resends_after_cooldown(monkeypatch):
    init_db()
    _reset_alert_events()
    captured = {"calls": 0}

    class _Settings:
        monitoring_alert_webhook_url = "https://example.test/hook"
        monitoring_alert_webhook_timeout_seconds = 5
        monitoring_alert_dedupe_window_seconds = 60
        monitoring_alert_retry_attempts = 3
        monitoring_alert_retry_backoff_seconds = 0.0

    class _Response:
        status_code = 200

    def _fake_post(url, json, timeout):  # noqa: ARG001
        captured["calls"] += 1
        return _Response()

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())
    monkeypatch.setattr(alerting.httpx, "post", _fake_post)

    metrics = _sample_metrics(alerts=["source_low_volume:rakuma"])

    with SessionLocal() as session:
        first = alerting.dispatch_health_alerts(session, metrics)
        assert first.reason == "sent"

        last_event = session.scalar(
            select(HealthAlertEvent)
            .where(HealthAlertEvent.reason == "sent")
            .order_by(HealthAlertEvent.created_at.desc())
            .limit(1)
        )
        assert last_event is not None
        last_event.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.add(last_event)
        session.commit()

        second = alerting.dispatch_health_alerts(session, metrics)
        assert second.reason == "sent"
        assert captured["calls"] == 2


def test_health_alert_dispatch_endpoint_returns_response_shape():
    init_db()
    response = client.post("/health/alerts/dispatch?window_hours=24")
    assert response.status_code == 200

    payload = response.json()
    assert "sent" in payload
    assert "reason" in payload
    assert "alert_count" in payload
    assert "deduped" in payload
    assert "alert_signature" in payload


def test_dispatch_health_alerts_retries_transient_5xx(monkeypatch):
    init_db()
    _reset_alert_events()
    captured = {"calls": 0}

    class _Settings:
        monitoring_alert_webhook_url = "https://example.test/hook"
        monitoring_alert_webhook_timeout_seconds = 5
        monitoring_alert_dedupe_window_seconds = 0
        monitoring_alert_retry_attempts = 3
        monitoring_alert_retry_backoff_seconds = 0.0

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    def _fake_post(url, json, timeout):  # noqa: ARG001
        captured["calls"] += 1
        if captured["calls"] < 3:
            return _Response(503)
        return _Response(200)

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())
    monkeypatch.setattr(alerting.httpx, "post", _fake_post)

    with SessionLocal() as session:
        result = alerting.dispatch_health_alerts(session, _sample_metrics(alerts=["parse_completeness_low"]))

    assert result.sent is True
    assert result.reason == "sent"
    assert captured["calls"] == 3
