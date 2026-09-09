"""Regression tests for the token pricing pipeline (`market_data`)."""

import httpx
import pytest

from app.services import market_data
from app.services.market_data import token_market_data


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean_cache():
    market_data._cache.clear()
    yield
    market_data._cache.clear()


@pytest.mark.asyncio
async def test_token_price_matches_mixed_case_solana_addresses(monkeypatch):
    """CoinGecko echoes Solana base58 addresses back in mixed case while the
    lookup keys are lower-cased — the mapping must be case-insensitive or
    every Solana token silently prices to None."""
    original = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"

    async def fake_get(self, url, **kwargs):
        assert "simple/token_price/solana" in url
        return _FakeResponse({original: {
            "usd": 0.004, "usd_market_cap": 1234.5, "usd_24h_vol": 99.0,
            "usd_24h_change": 1.5,
        }})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    settings = type("S", (), {
        "coingecko_api_key": "test-key",
        "coingecko_api_base_url": "https://api.coingecko.com/api/v3",
        "request_timeout_seconds": 5,
    })()
    market = await token_market_data(
        [{"token": "PUMP", "chain": "solana", "address": original}], settings)
    values = market[("solana", original.lower())]
    assert values["usd"] == 0.004
    assert values["usd_market_cap"] == 1234.5


@pytest.mark.asyncio
async def test_geckoterminal_fallback_prices_uncached_tokens(monkeypatch):
    """Tokens CoinGecko misses fall through to the GeckoTerminal bulk call."""
    async def fake_get(self, url, **kwargs):
        if "simple/token_price" in url:
            return _FakeResponse({})
        assert "/networks/solana/tokens/multi/" in url
        return _FakeResponse({"data": [{
            "id": "solana_9wqFeriebALdCP6v4iveE7UDycrVAFJDggt3ykE",
            "attributes": {"price_usd": "0.5", "market_cap_usd": "1000",
                           "volume_usd": {"h24": "250"}},
        }]})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    settings = type("S", (), {
        "coingecko_api_key": "test-key",
        "coingecko_api_base_url": "https://api.coingecko.com/api/v3",
        "request_timeout_seconds": 5,
    })()
    market = await token_market_data(
        [{"token": "XYZ", "chain": "solana", "address": "9wqFeriebALdCP6v4iveE7UDycrVAFJDggt3ykE"}],
        settings)
    values = market[("solana", "9wqferiebaldcp6v4ivee7udycrvafjdggt3yke")]
    assert values["usd"] == 0.5
    assert values["usd_24h_vol"] == 250.0
