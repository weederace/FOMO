import pytest

from app.config.settings import Settings
from app.providers.base import UnsupportedProviderError
from app.providers.crawl import CrawlProvider
from app.providers.factory import create_provider
from app.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic_and_normalized() -> None:
    traders = await MockProvider(seed=1, count=3).get_leaderboard()
    assert len(traders) == 3
    assert traders[0].platform == "mock"
    assert traders[0].total_trades is not None


@pytest.mark.asyncio
async def test_unsupported_capability_is_explicit() -> None:
    with pytest.raises(UnsupportedProviderError):
        await MockProvider().stream_events().__anext__()


def test_crawl_is_the_default_provider() -> None:
    assert isinstance(create_provider(Settings()), CrawlProvider)


def test_crawl_aliases_resolve_to_the_same_provider() -> None:
    for name in ("crawl", "fomo-crawl", "browser"):
        assert isinstance(create_provider(Settings(data_provider=name)), CrawlProvider)


def test_unknown_provider_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported DATA_PROVIDER"):
        create_provider(Settings(data_provider="scraper"))
