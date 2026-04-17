from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import model_validator
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
    worker_priority_interval_seconds: int = 300
    worker_idle_sleep_seconds: int = 10
    worker_ending_auction_window_hours: int = 24
    worker_priority_window_hours: int = 2
    priority_score_threshold: float = 0.55
    priority_value_reference_jpy_ceiling: int = 180000

    resale_model_artifact_path: str = "models/resale/baseline_v1.json"
    auction_model_artifact_path: str = "models/yahoo-auction/baseline_v1.json"
    model_version_root: str = "models/versions"
    model_active_pointer_resale: str = "models/resale/active_pointer.txt"
    model_active_pointer_auction: str = "models/yahoo-auction/active_pointer.txt"
    baseline_eval_report_path: str = "models/eval/baseline_eval_v1.json"
    baseline_eval_min_rows: int = 5
    baseline_eval_resale_max_mape: float = 0.5
    baseline_eval_auction_max_mape: float = 0.4
    baseline_eval_require_holdout: bool = True
    baseline_eval_bootstrap_samples: int = 1000
    baseline_eval_significance_alpha: float = 0.05

    monitoring_min_source_count: int = 1
    monitoring_min_parse_completeness: float = 0.65
    monitoring_min_non_discard_rate: float = 0.1
    monitoring_max_false_positive_rate: float = 0.6
    monitoring_alert_webhook_url: str = ""
    monitoring_alert_webhook_timeout_seconds: int = 10
    monitoring_alert_dedupe_window_seconds: int = 3600
    monitoring_alert_retry_attempts: int = 3
    monitoring_alert_retry_backoff_seconds: float = 1.0
    monitoring_max_model_age_hours: int = 24 * 30
    monitoring_max_listing_staleness_hours: int = 12
    worker_dispatch_health_alerts: bool = False
    worker_health_alert_window_hours: int = 24

    cors_allow_origins: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )
    cors_allow_methods: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    cors_allow_headers: str = "Authorization,Content-Type,Accept,Origin,X-Requested-With"

    fixture_listings_path: str = "data/fixtures/listings_sample.json"
    taxonomy_seed_path: str = "data/taxonomy/taxonomy_v1_seed.csv"
    taxonomy_feedback_types_path: str = "data/taxonomy/taxonomy_feedback_types.jsonl"
    feedback_pricing_labels_path: str = "data/labeled/raw/pen_swap_sales_feedback.jsonl"
    reports_dir: str = "data/reports"
    object_store_root: str = "data/object_store"
    object_store_enable_capture: bool = False
    object_store_capture_policy: str = "scored_or_ending_soon"
    object_store_generate_thumbnails: bool = False
    object_store_thumbnail_max_px: int = 320
    image_classifier_enabled: bool = False
    image_embedding_model_name: str = "local-hash-v1"
    image_classifier_blend_min_confidence: float = 0.6
    classification_calibration_min_rows: int = 30
    classification_calibration_bin_count: int = 10
    proxy_coupon_max_exact_stackable: int = 16
    proxy_coupon_fallback_top_stackable: int = 12
    proxy_first_time_user_penalty_jpy: int = 350
    resale_brand_min_samples: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "Settings":
        if not 0.0 <= self.min_profit_pct <= 1.0:
            raise ValueError("MIN_PROFIT_PCT must be in [0, 1]")
        if not 0.0 <= self.potential_min <= 1.0:
            raise ValueError("POTENTIAL_MIN must be in [0, 1]")
        if not 0.0 <= self.confident_min <= 1.0:
            raise ValueError("CONFIDENT_MIN must be in [0, 1]")
        if self.confident_min < self.potential_min:
            raise ValueError("CONFIDENT_MIN must be greater than or equal to POTENTIAL_MIN")
        if not 0.0 <= self.monitoring_min_non_discard_rate <= 1.0:
            raise ValueError("MONITORING_MIN_NON_DISCARD_RATE must be in [0, 1]")
        if not 0.0 <= self.monitoring_max_false_positive_rate <= 1.0:
            raise ValueError("MONITORING_MAX_FALSE_POSITIVE_RATE must be in [0, 1]")
        if self.monitoring_alert_retry_attempts < 1:
            raise ValueError("MONITORING_ALERT_RETRY_ATTEMPTS must be >= 1")
        if self.monitoring_alert_retry_backoff_seconds < 0:
            raise ValueError("MONITORING_ALERT_RETRY_BACKOFF_SECONDS must be >= 0")
        if self.monitoring_max_model_age_hours < 1:
            raise ValueError("MONITORING_MAX_MODEL_AGE_HOURS must be >= 1")
        if self.monitoring_max_listing_staleness_hours < 1:
            raise ValueError("MONITORING_MAX_LISTING_STALENESS_HOURS must be >= 1")
        if self.baseline_eval_bootstrap_samples < 100:
            raise ValueError("BASELINE_EVAL_BOOTSTRAP_SAMPLES must be >= 100")
        if not 0.0 < self.baseline_eval_significance_alpha < 1.0:
            raise ValueError("BASELINE_EVAL_SIGNIFICANCE_ALPHA must be in (0, 1)")
        if self.worker_priority_interval_seconds < 60:
            raise ValueError("WORKER_PRIORITY_INTERVAL_SECONDS must be >= 60")
        if self.worker_priority_window_hours < 1:
            raise ValueError("WORKER_PRIORITY_WINDOW_HOURS must be >= 1")
        if not 0.0 <= self.priority_score_threshold <= 1.0:
            raise ValueError("PRIORITY_SCORE_THRESHOLD must be in [0, 1]")
        if self.priority_value_reference_jpy_ceiling < 1:
            raise ValueError("PRIORITY_VALUE_REFERENCE_JPY_CEILING must be >= 1")
        if self.object_store_thumbnail_max_px < 32:
            raise ValueError("OBJECT_STORE_THUMBNAIL_MAX_PX must be >= 32")
        if not 0.0 <= self.image_classifier_blend_min_confidence <= 1.0:
            raise ValueError("IMAGE_CLASSIFIER_BLEND_MIN_CONFIDENCE must be in [0, 1]")
        if self.classification_calibration_min_rows < 5:
            raise ValueError("CLASSIFICATION_CALIBRATION_MIN_ROWS must be >= 5")
        if self.classification_calibration_bin_count < 2:
            raise ValueError("CLASSIFICATION_CALIBRATION_BIN_COUNT must be >= 2")
        if self.resale_brand_min_samples < 1:
            raise ValueError("RESALE_BRAND_MIN_SAMPLES must be >= 1")
        if self.proxy_coupon_max_exact_stackable < 1:
            raise ValueError("PROXY_COUPON_MAX_EXACT_STACKABLE must be >= 1")
        if self.proxy_coupon_fallback_top_stackable < 1:
            raise ValueError("PROXY_COUPON_FALLBACK_TOP_STACKABLE must be >= 1")
        if self.proxy_coupon_fallback_top_stackable > self.proxy_coupon_max_exact_stackable:
            raise ValueError(
                "PROXY_COUPON_FALLBACK_TOP_STACKABLE must be <= PROXY_COUPON_MAX_EXACT_STACKABLE"
            )
        if self.proxy_first_time_user_penalty_jpy < 0:
            raise ValueError("PROXY_FIRST_TIME_USER_PENALTY_JPY must be >= 0")
        allowed_capture_policies = {
            "none",
            "all",
            "scored_only",
            "ending_soon_only",
            "scored_or_ending_soon",
        }
        if self.object_store_capture_policy not in allowed_capture_policies:
            raise ValueError(
                "OBJECT_STORE_CAPTURE_POLICY must be one of: "
                "none, all, scored_only, ending_soon_only, scored_or_ending_soon"
            )
        try:
            ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"DEFAULT_TIMEZONE is not a valid IANA timezone: {self.default_timezone}") from exc
        webhook_url = self.monitoring_alert_webhook_url.strip()
        if webhook_url:
            parsed = urlparse(webhook_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("MONITORING_ALERT_WEBHOOK_URL must be a valid http(s) URL")
        if "*" in [method.strip() for method in self.cors_allow_methods.split(",")]:
            raise ValueError("CORS_ALLOW_METHODS cannot contain '*'")
        if "*" in [header.strip() for header in self.cors_allow_headers.split(",")]:
            raise ValueError("CORS_ALLOW_HEADERS cannot contain '*'")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
