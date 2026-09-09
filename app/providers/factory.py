from app.config.settings import Settings
from app.providers.base import BaseProvider
from app.providers.crawl import CrawlProvider
from app.providers.fomo import FomoProvider
from app.providers.mock import MockProvider

CRAWL_NAMES = {"crawl", "fomo-crawl", "browser"}
API_NAMES = {"fomo", "fomoapi"}


def create_provider(settings: Settings) -> BaseProvider:
    provider_name = settings.data_provider.lower()
    if provider_name == "mock":
        return MockProvider()
    if provider_name in CRAWL_NAMES:
        return CrawlProvider(
            settings.crawl_url,
            settings.crawl_leaderboard_window,
            settings.crawl_leaderboard_limit,
            settings.browser_executable_path,
            settings.browser_headless,
            settings.crawl_render_timeout_seconds,
            settings.crawl_cache_seconds,
            settings.crawl_alert_limit,
            settings.crawl_respect_robots,
        )
    if provider_name in API_NAMES:
        return FomoProvider(
            settings.fomo_api_base_url,
            settings.fomo_api_key,
            settings.request_timeout_seconds,
            settings.rate_limit_per_second,
        )
    raise ValueError(
        f"Unsupported DATA_PROVIDER: {settings.data_provider}. "
        f"Use crawl, mock, or {'/'.join(sorted(API_NAMES))}."
    )
