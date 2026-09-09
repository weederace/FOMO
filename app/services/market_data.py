from __future__ import annotations

import time
from typing import Any

import httpx

from app.config.settings import Settings
from app.providers.quota import QuotaExceeded, RequestQuota

PLATFORMS = {"ethereum": "ethereum", "base": "base", "bsc": "binance-smart-chain", "solana": "solana"}
GECKO_NETWORKS = {"ethereum": "eth", "base": "base", "bsc": "bsc", "solana": "solana"}
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_coingecko_quota = RequestQuota(per_second=2, window_seconds=60, window_limit=60)


async def token_market_data(tokens: list[dict], settings: Settings) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[str, set[str]] = {}
    if settings.coingecko_api_key:
        for item in tokens:
            address = item.get("address")
            platform = PLATFORMS.get(item.get("chain"))
            if address and platform:
                grouped.setdefault(platform, set()).add(address.lower())
    result: dict[tuple[str, str], dict[str, Any]] = {}
    # GeckoTerminal's Cloudflare edge 403s requests without a browser-ish
    # User-Agent (error 1010), so every call below must carry one.
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, trust_env=False,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
    ) as client:
        for platform, addresses in grouped.items():
            uncached = [address for address in addresses if (platform, address) not in _cache or time.monotonic() - _cache[(platform, address)][0] >= 60]
            if uncached:
                try:
                    await _coingecko_quota.acquire()
                    response = await client.get(
                        f"{settings.coingecko_api_base_url.rstrip('/')}/simple/token_price/{platform}",
                        params={
                            "contract_addresses": ",".join(uncached), "vs_currencies": "usd",
                            "include_market_cap": "true", "include_24hr_vol": "true",
                            "include_24hr_change": "true", "include_last_updated_at": "true",
                        },
                        headers={"x-cg-demo-api-key": settings.coingecko_api_key},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    # CoinGecko echoes addresses back in their original case;
                    # Solana base58 is case-sensitive, so match case-insensitively.
                    payload_by_lower = {str(key).lower(): value for key, value in payload.items()}
                    for address in uncached:
                        _cache[(platform, address)] = (time.monotonic(), payload_by_lower.get(address, {}))
                except (QuotaExceeded, httpx.HTTPError, ValueError):
                    pass
            for address in addresses:
                result[(platform, address)] = _cache.get((platform, address), (0, {}))[1]
        # CoinGecko doesn't index every new or DEX-only token. GeckoTerminal is
        # the fallback for those contracts and exposes pool-derived market data.
        # Only tokens that CoinGecko actually missed are fetched — GeckoTerminal
        # exposes a bulk /tokens/multi endpoint, so one request covers many
        # addresses instead of a per-token call that made rankings crawl.
        missing = [
            item for item in tokens
            if str(item.get("address") or "") and PLATFORMS.get(item.get("chain"))
            and not result.get((PLATFORMS[item["chain"]], str(item["address"]).lower()), {}).get("usd")
        ]
        by_network: dict[str, list[str]] = {}
        for item in missing:
            network = GECKO_NETWORKS.get(item["chain"])
            address = str(item["address"]).lower()
            cached = _cache.get((f"gecko:{network}", address))
            if cached and time.monotonic() - cached[0] < 60:
                result[(PLATFORMS[item["chain"]], address)] = cached[1]
                continue
            by_network.setdefault(network, []).append(address)
        NETWORK_PLATFORMS = {"eth": "ethereum", "base": "base", "bsc": "binance-smart-chain", "solana": "solana"}
        for network, addresses in by_network.items():
            platform = NETWORK_PLATFORMS.get(network)
            if platform is None:
                continue
            for chunk_start in range(0, len(addresses), 30):
                chunk = addresses[chunk_start : chunk_start + 30]
                try:
                    response = await client.get(
                        f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/multi/{'%2C'.join(chunk)}"
                    )
                    response.raise_for_status()
                    payload = response.json().get("data") or []
                except (httpx.HTTPError, ValueError):
                    payload = []
                found: dict[str, dict[str, Any]] = {}
                for entry in payload if isinstance(payload, list) else []:
                    attributes = entry.get("attributes") or {}
                    # GeckoTerminal ids keep the address's original case; the
                    # lookup keys are lower-cased, so normalize here too.
                    address = str(entry.get("id") or "").split("_", 1)[-1].lower()
                    values = {
                        "usd": float(attributes["price_usd"]) if attributes.get("price_usd") else None,
                        "usd_market_cap": float(attributes["market_cap_usd"]) if attributes.get("market_cap_usd") else None,
                        "usd_24h_vol": float((attributes.get("volume_usd") or {}).get("h24"))
                        if (attributes.get("volume_usd") or {}).get("h24") else None,
                    }
                    found[address] = values
                    _cache[(f"gecko:{network}", address)] = (time.monotonic(), values)
                    result[(platform, address)] = values
                for address in chunk:
                    if address not in found:
                        result[(platform, address)] = {}
                        _cache[(f"gecko:{network}", address)] = (time.monotonic(), {})
    return result
