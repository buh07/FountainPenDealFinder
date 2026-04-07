from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    app_port: int = 8000
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/fountain_pen"
    auto_create_tables: bool = False
    redis_url: str = "redis://localhost:6379/0"
    default_timezone: str = "Asia/Tokyo"
    min_profit_jpy: int = 10000
    min_profit_pct: float = 0.25
    confident_min: float = 0.75
    potential_min: float = 0.45
    use_fixture_fallback: bool = True
    ingestion_retry_attempts: int = 3
    ingestion_retry_backoff_seconds: float = 1.0
    ingestion_parse_min_completeness: float = 0.55
    ingestion_parse_min_valid_rows: int = 1

    yahoo_auctions_enabled: bool = True
    yahoo_auctions_base_url: str = "https://auctions.yahoo.co.jp"
    yahoo_auctions_search_path: str = "/search/search"
    yahoo_auctions_keyword: str = "万年筆"
    yahoo_auctions_max_results: int = 60
    yahoo_auctions_timeout_seconds: int = 20
    yahoo_auctions_verify_ssl: bool = True
    yahoo_auctions_request_interval_seconds: float = 0.0

    yahoo_flea_market_enabled: bool = True
    yahoo_flea_market_base_url: str = "https://paypayfleamarket.yahoo.co.jp"
    yahoo_flea_market_search_path: str = "/search"
    yahoo_flea_market_keyword: str = "万年筆"
    yahoo_flea_market_max_results: int = 60
    yahoo_flea_market_timeout_seconds: int = 20
    yahoo_flea_market_verify_ssl: bool = True
    yahoo_flea_market_request_interval_seconds: float = 0.0

    mercari_enabled: bool = True
    mercari_base_url: str = "https://jp.mercari.com"
    mercari_search_path: str = "/search"
    mercari_keyword: str = "万年筆"
    mercari_max_results: int = 60
    mercari_timeout_seconds: int = 20
    mercari_verify_ssl: bool = True
    mercari_request_interval_seconds: float = 0.0

    rakuma_enabled: bool = True
    rakuma_base_url: str = "https://fril.jp"
    rakuma_search_path: str = "/s"
    rakuma_keyword: str = "万年筆"
    rakuma_max_results: int = 60
    rakuma_timeout_seconds: int = 20
    rakuma_verify_ssl: bool = True
    rakuma_request_interval_seconds: float = 0.0

    worker_enable_scheduler: bool = False
    worker_fixed_source_interval_seconds: int = 3600
    worker_ending_auctions_interval_seconds: int = 900
    worker_idle_sleep_seconds: int = 10
    worker_ending_auction_window_hours: int = 24

    resale_model_artifact_path: str = "models/resale/baseline_v1.json"
    auction_model_artifact_path: str = "models/yahoo-auction/baseline_v1.json"
    baseline_eval_report_path: str = "models/eval/baseline_eval_v1.json"
    baseline_eval_min_rows: int = 5
    baseline_eval_resale_max_mape: float = 0.5
    baseline_eval_auction_max_mape: float = 0.4

    monitoring_min_source_count: int = 1
    monitoring_min_parse_completeness: float = 0.65
    monitoring_min_non_discard_rate: float = 0.1
    monitoring_max_false_positive_rate: float = 0.6
    monitoring_alert_webhook_url: str = ""
    monitoring_alert_webhook_timeout_seconds: int = 10
    worker_dispatch_health_alerts: bool = False
    worker_health_alert_window_hours: int = 24

    fixture_listings_path: str = "data/fixtures/listings_sample.json"
    reports_dir: str = "data/reports"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
