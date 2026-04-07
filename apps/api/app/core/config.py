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

    yahoo_auctions_enabled: bool = True
    yahoo_auctions_base_url: str = "https://auctions.yahoo.co.jp"
    yahoo_auctions_search_path: str = "/search/search"
    yahoo_auctions_keyword: str = "万年筆"
    yahoo_auctions_max_results: int = 60
    yahoo_auctions_timeout_seconds: int = 20
    yahoo_auctions_verify_ssl: bool = True
    yahoo_auctions_request_interval_seconds: float = 0.0

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
