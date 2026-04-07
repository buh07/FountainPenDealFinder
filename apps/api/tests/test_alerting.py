from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
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


def test_dispatch_health_alerts_returns_no_alerts_without_destinations():
    result = alerting.dispatch_health_alerts(_sample_metrics(alerts=[]))

    assert result.sent is False
    assert result.reason == "no_alerts"
    assert result.alert_count == 0


def test_dispatch_health_alerts_returns_webhook_not_configured_for_alerts(monkeypatch):
    class _Settings:
        monitoring_alert_webhook_url = ""
        monitoring_alert_webhook_timeout_seconds = 10

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())

    result = alerting.dispatch_health_alerts(_sample_metrics(alerts=["parse_completeness_low"]))

    assert result.sent is False
    assert result.reason == "webhook_not_configured"
    assert result.alert_count == 1


def test_dispatch_health_alerts_posts_to_webhook(monkeypatch):
    captured = {"url": None, "json": None, "timeout": None}

    class _Settings:
        monitoring_alert_webhook_url = "https://example.test/hook"
        monitoring_alert_webhook_timeout_seconds = 5

    class _Response:
        status_code = 200

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(alerting, "get_settings", lambda: _Settings())
    monkeypatch.setattr(alerting.httpx, "post", _fake_post)

    result = alerting.dispatch_health_alerts(_sample_metrics(alerts=["source_low_volume:rakuma"]))

    assert result.sent is True
    assert result.reason == "sent"
    assert result.destination == "https://example.test/hook"
    assert result.status_code == 200
    assert captured["url"] == "https://example.test/hook"
    assert captured["timeout"] == 5
    assert captured["json"]["type"] == "health_alert"


def test_health_alert_dispatch_endpoint_returns_response_shape():
    init_db()
    response = client.post("/health/alerts/dispatch?window_hours=24")
    assert response.status_code == 200

    payload = response.json()
    assert "sent" in payload
    assert "reason" in payload
    assert "alert_count" in payload
