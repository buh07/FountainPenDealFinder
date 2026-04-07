import httpx

from ..core.config import get_settings
from ..schemas import HealthAlertDispatchResponse, HealthMetricsResponse


def dispatch_health_alerts(metrics: HealthMetricsResponse) -> HealthAlertDispatchResponse:
    alert_count = len(metrics.alerts)
    if alert_count == 0:
        return HealthAlertDispatchResponse(
            sent=False,
            reason="no_alerts",
            alert_count=0,
        )

    settings = get_settings()
    destination = settings.monitoring_alert_webhook_url.strip()
    if not destination:
        return HealthAlertDispatchResponse(
            sent=False,
            reason="webhook_not_configured",
            alert_count=alert_count,
        )

    payload = {
        "type": "health_alert",
        "generated_at": metrics.generated_at.isoformat(),
        "window_hours": metrics.window_hours,
        "alerts": metrics.alerts,
        "source_counts": metrics.source_counts,
        "parse_completeness_avg": metrics.parse_completeness_avg,
        "non_discard_rate": metrics.non_discard_rate,
        "false_positive_rate": metrics.false_positive_rate,
        "baseline_eval_pass": metrics.baseline_eval_pass,
    }

    try:
        response = httpx.post(
            destination,
            json=payload,
            timeout=max(1, settings.monitoring_alert_webhook_timeout_seconds),
        )
    except httpx.HTTPError:
        return HealthAlertDispatchResponse(
            sent=False,
            reason="request_failed",
            alert_count=alert_count,
            destination=destination,
        )

    if 200 <= response.status_code < 300:
        return HealthAlertDispatchResponse(
            sent=True,
            reason="sent",
            alert_count=alert_count,
            destination=destination,
            status_code=response.status_code,
        )

    return HealthAlertDispatchResponse(
        sent=False,
        reason="non_2xx_response",
        alert_count=alert_count,
        destination=destination,
        status_code=response.status_code,
    )
