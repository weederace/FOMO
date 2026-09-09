from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./fomo.db"
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    fomo_base_url: str = "https://fomo.family/"
    crawl_url: str = "https://fomoapi.io/"
    crawl_leaderboard_window: str = "24h"
    crawl_leaderboard_limit: int = Field(default=10, ge=1, le=100)
    crawl_alert_limit: int = Field(default=25, ge=1, le=100)
    crawl_cache_seconds: int = Field(default=60, ge=0)
    crawl_render_timeout_seconds: float = Field(default=45, gt=0)
    crawl_respect_robots: bool = True
    browser_executable_path: str | None = None
    browser_headless: bool = True
    fomo_api_base_url: str = "https://api.fomoapi.io"
    fomo_api_key: str | None = None
    etherscan_api_key: str | None = None
    solscan_api_key: str | None = None
    cryptoapis_api_key: str | None = None
    coingecko_api_key: str | None = None
    coingecko_api_base_url: str = "https://api.coingecko.com/api/v3"
    eth_rpc_url: str | None = None
    base_rpc_url: str | None = None
    bsc_rpc_url: str | None = None
    solana_rpc_url: str | None = None
    chain_scan_enabled: bool = False
    chain_scan_lookback_blocks: int = Field(default=2000, ge=100, le=100_000)
    chain_scan_wallet_limit: int = Field(default=50, ge=1, le=500)
    chain_scan_interval_seconds: int = Field(default=60, ge=10)
    eth_daily_request_limit: int = Field(default=100_000, ge=1)
    evm_requests_per_second: int = Field(default=5, ge=1)
    solana_monthly_request_limit: int = Field(default=10_000_000, ge=1)
    solana_requests_per_second: int = Field(default=1000, ge=1)
    collection_interval_seconds: int = Field(default=300, ge=1)
    leaderboard_interval_seconds: int = Field(default=300, ge=1)
    trader_refresh_interval_seconds: int = Field(default=900, ge=1)
    trade_refresh_interval_seconds: int = Field(default=900, ge=1)
    score_interval_seconds: int = Field(default=300, ge=1)
    alert_cooldown_seconds: int = Field(default=3600, ge=1)
    max_concurrent_provider_requests: int = Field(default=8, ge=1, le=64)
    detail_collection_limit: int = Field(default=50, ge=0, le=150)
    mock_mode: bool = False
    data_provider: str = "crawl"
    log_level: str = "INFO"
    request_timeout_seconds: float = Field(default=20, gt=0)
    rate_limit_per_second: float = Field(default=1, gt=0)
    watchlist_score_threshold: float = Field(default=75, ge=0, le=100)
    watchlist_confidence_threshold: float = Field(default=70, ge=0, le=100)
    min_history_points: int = Field(default=3, ge=3)
    emerging_min_score: float = Field(default=60, ge=0, le=100)
    emerging_min_confidence: float = Field(default=60, ge=0, le=100)
    emerging_min_score_growth: float = Field(default=10, ge=0, le=100)
    emerging_lookback_days: int = Field(default=7, ge=1)
    emerging_min_history: int = Field(default=3, ge=3)
    # Whale Flow: only trades of at least this USD size enter the 24h feed.
    flow_min_usd: int = Field(default=100, ge=0)

    # Optional GMGN integration (wallet intelligence + market radar). The CLI
    # authenticates itself from GMGN_API_KEY; gmgn_enabled gates every call.
    gmgn_enabled: bool = False
    gmgn_api_key: str | None = None
    gmgn_cli_command: str = "gmgn-cli"
    gmgn_request_timeout_seconds: float = Field(default=60, gt=0)
    gmgn_enrich_interval_seconds: int = Field(default=300, ge=30)
    gmgn_market_interval_seconds: int = Field(default=60, ge=15)
    gmgn_market_cache_seconds: int = Field(default=60, ge=10)
    gmgn_wallet_limit: int = Field(default=50, ge=1, le=500)
    gmgn_wallet_batch_size: int = Field(default=10, ge=1, le=20)
    gmgn_activity_limit: int = Field(default=50, ge=1, le=200)
    gmgn_holdings_limit: int = Field(default=50, ge=1, le=200)
    gmgn_market_row_limit: int = Field(default=30, ge=1, le=100)
    gmgn_default_chain: str = "sol"
    # Watchlist: pinned tokens are re-queried on this clock (default 5 minutes).
    watchlist_interval_seconds: int = Field(default=300, ge=30)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
