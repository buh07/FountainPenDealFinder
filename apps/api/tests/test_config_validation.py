import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_rejects_confident_below_potential():
    with pytest.raises(ValidationError):
        Settings(
            confident_min=0.4,
            potential_min=0.5,
        )


def test_settings_rejects_out_of_range_min_profit_pct():
    with pytest.raises(ValidationError):
        Settings(min_profit_pct=1.2)


def test_settings_accepts_valid_thresholds():
    settings = Settings(
        min_profit_pct=0.25,
        potential_min=0.45,
        confident_min=0.75,
    )
    assert settings.confident_min >= settings.potential_min


def test_settings_rejects_invalid_timezone():
    with pytest.raises(ValidationError):
        Settings(default_timezone="Mars/Phobos")


def test_settings_rejects_invalid_webhook_url():
    with pytest.raises(ValidationError):
        Settings(monitoring_alert_webhook_url="ftp://example.com/hook")


def test_settings_rejects_wildcard_cors_methods_headers():
    with pytest.raises(ValidationError):
        Settings(cors_allow_methods="*")

    with pytest.raises(ValidationError):
        Settings(cors_allow_headers="*")
