from datetime import UTC, datetime

import httpx
import pytest

from app.config.settings import Settings
from app.providers.factory import create_provider
from app.providers.fomo import FomoProvider, FomoProviderError


@pytest.mark.asyncio
async def test_documented_leaderboard_is_normalized_without_raw_payload_leak():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/leaderboard/24h"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "capturedAt": datetime.now(UTC).isoformat(),
                "traders": [{
                    "rank": 1,
                    "handle": "alice",
                    "displayName": "Alice",
                    "pnlUsd": 1234,
                    "volumeUsd": 5000,
                    "trades": 12,
                    "followers": 9,
                    "wallets": {"evm": "0xabc"},
                }],
            },
        )

    provider = FomoProvider("https://api.fomoapi.io", "test-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.base_url)
    try:
        traders = await provider.get_leaderboard()
        assert traders[0].platform == "fomoapi"
        assert traders[0].platform_trader_id == "alice"
        assert traders[0].wallet_address == "0xabc"
    finally:
        await provider.aclose()


def test_fomo_provider_is_selected_without_falling_back_to_mock():
    provider = create_provider(Settings(data_provider="fomo", fomo_api_key="configured"))
    assert isinstance(provider, FomoProvider)


@pytest.mark.asyncio
async def test_provider_surfaces_auth_error_without_secret():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid"})

    provider = FomoProvider("https://api.fomoapi.io", "test-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.base_url)
    try:
        with pytest.raises(FomoProviderError) as error:
            await provider.get_leaderboard(limit=1)
        assert error.value.status_code == 401
        assert "test-key" not in str(error.value)
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_keyless_alert_feed_is_normalized():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"alerts": [{"id": "a1", "type": "buy", "trader": "alice", "usdValue": 12.5}]})

    provider = FomoProvider("https://api.fomoapi.io")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.base_url)
    try:
        alerts = await provider.get_alerts(2)
        assert alerts[0]["id"] == "a1"
        assert alerts[0]["usdValue"] == 12.5
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_credit_exhaustion_disables_authenticated_capabilities():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "credits_exhausted"})

    provider = FomoProvider("https://api.fomoapi.io", "test-key")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=provider.base_url)
    try:
        with pytest.raises(FomoProviderError):
            await provider.get_trader("alice")
        assert provider.capabilities()["user_profile"] is False
    finally:
        await provider.aclose()
