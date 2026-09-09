"""Token watchlist: user-pinned tokens refreshed on a 5-minute clock.

The worker (or the API on demand) pulls fresh token data from GMGN for every
pinned (chain, address) pair, caches the flattened snapshot in the row's
`payload` and raises a `WATCHLIST_MOVE` alert when the 5m/1h change crosses
the movement threshold so pinned tokens behave like a mini radar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import WatchlistItem
from app.providers.gmgn import GmgnClient
from app.services.alert_service import AlertService
from app.services.gmgn_market import _number, _token_row

LOGGER = logging.getLogger("watchlist")

# A pinned token counts as "moving" when its 5m or 1h change crosses this
# percent; the alert is deduplicated by GMGN's own event-id logic downstream.
MOVE_ALERT_PERCENT = 10.0


def canonical_chain(chain: str | None) -> str:
    """Accept gmgn-cli ids and the UI's names; store the canonical form."""
    return {"eth": "eth", "ethereum": "eth", "sol": "sol", "solana": "sol",
            "base": "base", "bsc": "bsc"}.get(str(chain or "").lower(), "sol")


async def add_token(session: AsyncSession, chain: str, address: str,
                    symbol: str | None = None, note: str | None = None) -> WatchlistItem:
    """Pin a token. Re-adding an existing pair refreshes symbol/note instead."""
    chain = canonical_chain(chain)
    existing = (await session.scalars(
        select(WatchlistItem).where(
            WatchlistItem.chain == chain, WatchlistItem.address == address)
    )).first()
    if existing:
        existing.symbol = symbol or existing.symbol
        existing.note = note if note is not None else existing.note
        return existing
    item = WatchlistItem(chain=chain, address=address, symbol=symbol, note=note)
    session.add(item)
    await session.flush()
    return item


async def remove_token(session: AsyncSession, chain: str, address: str) -> bool:
    chain = canonical_chain(chain)
    item = (await session.scalars(
        select(WatchlistItem).where(
            WatchlistItem.chain == chain, WatchlistItem.address == address)
    )).first()
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


async def list_items(session: AsyncSession) -> list[WatchlistItem]:
    return list((await session.scalars(
        select(WatchlistItem).order_by(WatchlistItem.created_at.desc())
    )).all())


async def refresh_watchlist(session: AsyncSession, settings: Settings,
                            client: GmgnClient,
                            alert_service: AlertService | None = None) -> dict[str, int]:
    """Re-query GMGN for every pinned token and cache the snapshot.

    Uses each item's canonical chain; one failed token never aborts the
    refresh. Returns counts for the worker log.
    """
    items = await list_items(session)
    refreshed = moved = errors = 0
    alert_service = alert_service or AlertService()
    for item in items:
        try:
            info = await client.token_info(item.chain, item.address)
            row = _token_row(item.chain, info if isinstance(info, dict) else {})
            # `token info` nests a rich `price` block (now, 1m/5m/1h/6h/24h
            # references, volumes) — flatten it into the row before falling
            # back to the kline feed for anything still missing.
            _flatten_price_block(info if isinstance(info, dict) else {}, row)
            if row.get("change_5m") is None or row.get("change_1h") is None:
                await _merge_kline_changes(client, item.chain, item.address, row)
            if row.get("address") is None:
                row["address"] = item.address
            item.payload = {**row, "refreshed_at": datetime.now(UTC).isoformat()}
            item.symbol = row.get("symbol") or item.symbol
            refreshed += 1
            if alert_service and _is_moving(row):
                moved += 1
                await alert_service.create_provider_alert(session, {
                    "type": "WATCHLIST_MOVE",
                    "chain": item.chain,
                    "address": item.address,
                    "symbol": row.get("symbol") or item.symbol,
                    "text": (
                        f"Watchlist {row.get('symbol') or item.address[:8]} is moving · "
                        f"5m {row.get('change_5m') or 0:+.2f}% · 1h {row.get('change_1h') or 0:+.2f}% · "
                        f"price {row.get('price_usd') or 0:.8g}"
                    ),
                    "usdValue": row.get("volume_usd"),
                    "source": "watchlist",
                    "capturedAt": datetime.now(UTC).isoformat(),
                })
        except Exception as exc:  # one dead token must not stop the sweep
            errors += 1
            LOGGER.warning("watchlist refresh failed %s/%s: %s",
                           item.chain, item.address[:10], exc)
    return {"items": len(items), "refreshed": refreshed, "moved": moved, "errors": errors}


def _flatten_price_block(info: dict[str, Any], row: dict[str, Any]) -> None:
    """Merge the nested `price` block of `token info` into the flat row.

    The block carries the current price plus 1m/5m/1h/6h/24h reference
    prices and per-window volumes — everything the watchlist card shows.
    """
    price_block = info.get("price") if isinstance(info.get("price"), dict) else {}
    if not price_block:
        return
    now_price = _number(price_block.get("price"))

    def _ref_change(key: str) -> float | None:
        reference = _number(price_block.get(key))
        if now_price is None or reference in (None, 0):
            return None
        return round((now_price - reference) / reference * 100, 4)

    if row.get("price_usd") is None and now_price is not None:
        row["price_usd"] = now_price
    if row.get("change_5m") is None:
        row["change_5m"] = _ref_change("price_5m")
    if row.get("change_1h") is None:
        row["change_1h"] = _ref_change("price_1h")
    if row.get("change_24h") is None:
        row["change_24h"] = _ref_change("price_24h")
    if row.get("volume_5m") is None:
        row["volume_5m"] = _number(price_block.get("volume_5m"))
    if row.get("volume_1h") is None:
        row["volume_1h"] = _number(price_block.get("volume_1h"))
    if row.get("volume_usd") is None:
        row["volume_usd"] = _number(price_block.get("volume_24h"))
    if row.get("swaps") is None:
        row["swaps"] = price_block.get("swaps_24h") if isinstance(price_block.get("swaps_24h"), int) else None


def _is_moving(row: dict[str, Any]) -> bool:
    for key in ("change_5m", "change_1h"):
        value = row.get(key)
        if isinstance(value, (int, float)) and abs(value) >= MOVE_ALERT_PERCENT:
            return True
    return False


async def _merge_kline_changes(client: GmgnClient, chain: str, address: str,
                               row: dict[str, Any]) -> None:
    """Fill change_5m/change_1h from the 1-minute kline feed."""
    try:
        candles = await client.market_kline(chain, address, resolution="1m")
    except Exception:
        return
    rows = [c for c in candles if isinstance(c, dict)]
    if len(rows) < 2:
        return

    def _ts(candle: dict) -> float:
        value = candle.get("timestamp") or candle.get("time") or candle.get("t") or 0
        try:
            value = float(value)
        except (TypeError, ValueError):
            return 0.0
        return value / 1000.0 if value > 1e12 else value  # ms → s

    def _close(candle: dict) -> float | None:
        for key in ("close", "c", "price"):
            if candle.get(key) is not None:
                try:
                    return float(candle[key])
                except (TypeError, ValueError):
                    continue
        return None

    now_ts = _ts(rows[-1])
    latest = _close(rows[-1])

    def change_over(seconds: int) -> float | None:
        target = now_ts - seconds
        reference = next((_close(c) for c in reversed(rows) if _ts(c) <= target), None)
        if latest is None or reference in (None, 0):
            return None
        return round((latest - reference) / reference * 100, 4)

    if row.get("change_5m") is None:
        row["change_5m"] = change_over(300)
    if row.get("change_1h") is None:
        row["change_1h"] = change_over(3600)
