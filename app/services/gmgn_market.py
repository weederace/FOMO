"""GMGN market radar: trending, trenches, and hot searches with a TTL cache.

The CLI enforces roughly one request per second and its data changes on the
order of minutes, so every endpoint is served from an in-process cache that a
background worker refreshes on its own clock. Alert events are raised once per
token per type via content-derived keys, exactly like the crawl alerts.
"""

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.providers.gmgn import GmgnClient, GmgnProviderError
from app.services.alert_service import AlertService

LOGGER = logging.getLogger(__name__)

CHAIN_EMOJI = {"sol": "◎", "bsc": "◆", "base": "▲", "eth": "Ξ"}


def _token_row(chain: str, row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one market row into the flat shape the UI/API consumes."""
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    address = row.get("address") or row.get("token_address") or token.get("address") or token.get("contract")
    symbol = (
        token.get("symbol") or row.get("symbol") or row.get("token_symbol")
        or token.get("name") or row.get("name")
    )
    return {
        "chain": chain,
        "symbol": str(symbol) if symbol else None,
        "address": str(address) if address else None,
        "market_cap_usd": _number(row.get("market_cap") or row.get("marketcap") or token.get("market_cap")),
        "liquidity_usd": _number(row.get("liquidity") or token.get("liquidity")),
        "volume_usd": _number(row.get("volume") or row.get("volume_5m")
                              or row.get("volume_24h") or row.get("usd_volume")),
        "swaps": row.get("swaps") if isinstance(row.get("swaps"), int) else None,
        "holders": row.get("holder_count") if isinstance(row.get("holder_count"), int) else None,
        "smart_degen_count": row.get("smart_degen_count")
        if isinstance(row.get("smart_degen_count"), int) else None,
        "renowned_count": row.get("renowned_count")
        if isinstance(row.get("renowned_count"), int) else None,
        "price_usd": _number(row.get("price") or token.get("price_usd")),
        # The CLI names the percent columns price_change_percent5m/1h (no
        # underscore before the interval); accept the older spellings too.
        "change_5m": _number(
            row.get("price_change_percent5m") or row.get("price_change_5m")
            or row.get("change_5m")
        ),
        "change_1h": _number(
            row.get("price_change_percent1h") or row.get("price_change_1h")
            or row.get("change_1h")
        ),
        "launchpad": row.get("launchpad_platform") or row.get("launchpad") or token.get("launchpad"),
        "created_at": row.get("created_at") or token.get("created_at"),
    }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GmgnMarketCache:
    """Process-level TTL cache so the API never waits on the CLI."""

    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        cached = self._store.get(key)
        if cached is None:
            return None
        fetched_at, payload = cached
        if time.monotonic() - fetched_at > self.ttl_seconds:
            return None
        return payload

    def set(self, key: str, payload: Any) -> None:
        self._store[key] = (time.monotonic(), payload)


_cache = GmgnMarketCache()


def market_cache() -> GmgnMarketCache:
    """The module-level cache shared by the worker and the API process."""
    return _cache


def alert_event_id(alert_type: str, chain: str, address: str | None) -> str:
    """Stable per-token id so repeated polls deduplicate."""
    return hashlib.sha256(f"{alert_type}|{chain}|{address or ''}".encode()).hexdigest()[:16]


async def refresh_market(session: AsyncSession, settings: Settings, client: GmgnClient | None = None) -> dict[str, int]:
    """Pull every market feed once, refresh the cache, and raise alerts.

    Called by the worker on its own interval. Any feed that fails is logged
    and skipped; the cache keeps serving the previous data.
    """
    if not settings.gmgn_enabled:
        return {"alerts": 0, "feeds": 0}
    client = client or GmgnClient(
        command=settings.gmgn_cli_command,
        request_timeout_seconds=settings.gmgn_request_timeout_seconds,
    )
    chain = settings.gmgn_default_chain
    limit = settings.gmgn_market_row_limit
    alert_service = AlertService()
    raised = 0
    feeds = 0

    # 1) trending tokens
    try:
        rows = await client.market_trending(chain, "5m", limit=limit)
        _cache.set("trending", [_token_row(chain, row) for row in rows])
        feeds += 1
        for row in rows:
            normalized = _token_row(chain, row)
            if not normalized.get("address"):
                continue
            event = {
                "type": "GMGN_TRENDING",
                "chain": chain,
                "address": normalized.get("address"),
                "symbol": normalized.get("symbol"),
                "text": (
                    f"{CHAIN_EMOJI.get(chain, '')}{normalized.get('symbol') or 'token'} is trending on "
                    f"{chain} · mcap ${normalized.get('market_cap_usd') or 0:,.0f} · "
                    f"vol ${normalized.get('volume_usd') or 0:,.0f} · "
                    f"{normalized.get('smart_degen_count') or 0} smart money"
                ),
                "usdValue": normalized.get("volume_usd"),
                "source": "gmgn",
                "capturedAt": datetime.now(UTC).isoformat(),
            }
            if await alert_service.create_provider_alert(
                session, event, event_id=alert_event_id("GMGN_TRENDING", chain, normalized.get("address"))
            ):
                raised += 1
    except GmgnProviderError as exc:
        LOGGER.warning("GMGN trending refresh failed: %s", exc)

    # 2) trenches (new + near graduation); completed is fetched but only cached
    try:
        buckets = await client.market_trenches(chain, limit=limit)
        _cache.set(
            "trenches",
            {
                kind: [_token_row(chain, row) for row in rows]
                for kind, rows in buckets.items()
            },
        )
        feeds += 1
        for kind, alert_type, text_prefix in (
            ("new_creation", "GMGN_NEW_TOKEN", "New token created on"),
            ("near_completion", "GMGN_NEAR_GRADUATION", "Near graduation on"),
        ):
            for row in buckets.get(kind, []):
                normalized = _token_row(chain, row)
                if not normalized.get("address"):
                    continue
                event = {
                    "type": alert_type,
                    "chain": chain,
                    "address": normalized.get("address"),
                    "symbol": normalized.get("symbol"),
                    "launchpad": normalized.get("launchpad"),
                    "text": (
                        f"{text_prefix} {normalized.get('launchpad') or chain}: "
                        f"{normalized.get('symbol') or 'token'} · "
                        f"mcap ${normalized.get('market_cap_usd') or 0:,.0f} · "
                        f"{normalized.get('smart_degen_count') or 0} smart money"
                    ),
                    "usdValue": normalized.get("market_cap_usd"),
                    "source": "gmgn",
                    "capturedAt": datetime.now(UTC).isoformat(),
                }
                if await alert_service.create_provider_alert(
                    session, event, event_id=alert_event_id(alert_type, chain, normalized.get("address"))
                ):
                    raised += 1
    except GmgnProviderError as exc:
        LOGGER.warning("GMGN trenches refresh failed: %s", exc)

    # 3) hot searches
    try:
        rows = await client.market_hot_searches("5m", limit=limit)
        _cache.set("hot_searches", [_token_row(chain, row) for row in rows])
        feeds += 1
        for row in rows:
            normalized = _token_row(chain, row)
            if not normalized.get("address"):
                continue
            event = {
                "type": "GMGN_HOT_SEARCH",
                "chain": normalized.get("chain") or chain,
                "address": normalized.get("address"),
                "symbol": normalized.get("symbol"),
                "text": (
                    f"Hot search: {normalized.get('symbol') or 'token'} "
                    f"({normalized.get('chain') or chain}) · "
                    f"mcap ${normalized.get('market_cap_usd') or 0:,.0f}"
                ),
                "usdValue": normalized.get("market_cap_usd"),
                "source": "gmgn",
                "capturedAt": datetime.now(UTC).isoformat(),
            }
            if await alert_service.create_provider_alert(
                session, event, event_id=alert_event_id("GMGN_HOT_SEARCH", chain, normalized.get("address"))
            ):
                raised += 1
    except GmgnProviderError as exc:
        LOGGER.warning("GMGN hot-searches refresh failed: %s", exc)

    return {"alerts": raised, "feeds": feeds}
