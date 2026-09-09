"""Pure calculations used by the live dashboard.

The functions in this module deliberately accept dictionaries so they can be used
by the API, the desktop client, and tests without coupling the UI to SQLAlchemy.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def whale_score(
    trader: dict[str, Any],
    *,
    present_windows: int = 1,
    total_windows: int = 4,
    thesis_feedback: int = 0,
) -> float:
    """Calculate the dashboard score from the requested 35/35/15/15 model."""
    pnl = _number(trader.get("pnl"))
    volume = _number(trader.get("volume"))
    trades = _number(trader.get("trades"))
    roi = (pnl / volume * 100) if volume > 0 else 0.0

    consistency = _clamp(present_windows / max(total_windows, 1) * 100)
    # 100% ROI is treated as the practical ceiling; negative ROI scores zero.
    capital_efficiency = _clamp(roi)
    strategy = _clamp(min(trades / 100 * 70, 70) + min(max(pnl, 0) / 10_000, 30))
    # Social reach is intentionally excluded. Only thesis feedback contributes to
    # the final 15% so follower count can never inflate a trader's score.
    thesis_signal = _clamp(thesis_feedback / 10 * 100)
    return round(consistency * 0.35 + capital_efficiency * 0.35 + strategy * 0.15 + thesis_signal * 0.15, 2)


def trader_archetype(trader: dict[str, Any], present_windows: int = 1) -> str:
    trades = _number(trader.get("trades"))
    volume = _number(trader.get("volume"))
    pnl = _number(trader.get("pnl"))
    roi = pnl / volume if volume > 0 else 0
    if trades <= 25 and volume <= 100_000 and roi >= 1:
        return "Sniper / Insider"
    if trades >= 1000 and volume >= 1_000_000 and 0 <= roi <= 0.25:
        return "HFT Bot"
    if present_windows >= 3 and pnl > 0:
        return "Solid Whale"
    return "Emerging Trader"


def snapshot_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, float]:
    previous = previous or {}
    return {
        "pnl_delta": round(_number(current.get("pnl")) - _number(previous.get("pnl")), 2),
        "trades_delta": round(_number(current.get("trades")) - _number(previous.get("trades")), 2),
        "volume_delta": round(_number(current.get("volume")) - _number(previous.get("volume")), 2),
    }


def classify_trade_event(side: Any, status: Any) -> dict[str, str]:
    """Distinguish a platform buy/sell from an inbound token receipt."""
    normalized_side = str(side or "").strip().lower().replace("-", "_")
    normalized_status = str(status or "").strip().lower().replace("-", "_")
    received_states = {"received", "receive", "transfer_in", "deposit", "airdrop", "claim"}
    if normalized_side in received_states or normalized_status in received_states:
        return {"event": "RECEIVED", "acquisition": "INBOUND_TRANSFER"}
    if normalized_side in {"buy", "bought", "long", "purchase"}:
        return {"event": "BUY", "acquisition": "PLATFORM_BUY"}
    if normalized_side in {"sell", "sold", "short"}:
        return {"event": "SELL", "acquisition": "PLATFORM_SELL"}
    return {"event": "UNKNOWN", "acquisition": "UNCLASSIFIED"}


def flow_summary(
    trades: list[dict[str, Any]], now: datetime | None = None, recent_minutes: int = 1440,
) -> dict[str, Any]:
    """Summarize whale events over `recent_minutes` plus 5m/1h volume changes.

    `recent_minutes` defaults to a full day so the feed always shows the
    latest whale activity, not just the last five minutes.
    """
    now = now or datetime.now().astimezone()
    normalized: list[tuple[dict[str, Any], datetime | None]] = []
    for trade in trades:
        timestamp = trade.get("executed_at") or trade.get("captured_at")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                timestamp = None
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=now.tzinfo)
        normalized.append((trade, timestamp))

    def in_window(timestamp: datetime | None, minutes: int, offset: int = 0) -> bool:
        end = now - timedelta(minutes=offset)
        start = end - timedelta(minutes=minutes)
        return timestamp is None or start <= timestamp <= end

    recent = [trade for trade, timestamp in normalized if in_window(timestamp, recent_minutes)]
    buys = [item for item in recent if str(item.get("side", "")).lower() == "buy"]
    by_token = Counter(str(item.get("token_symbol") or "UNKNOWN") for item in buys)
    amounts = Counter()
    for item in buys:
        amounts[str(item.get("token_symbol") or "UNKNOWN")] += _number(item.get("size_usd"))
    token_names = {
        str(item.get("token_symbol") or "UNKNOWN")
        for item, _timestamp in normalized
    }
    token_volume_changes = []
    for token in sorted(token_names):
        token_rows = [(item, timestamp) for item, timestamp in normalized
                      if str(item.get("token_symbol") or "UNKNOWN") == token]

        def volume(minutes: int, offset: int = 0, rows: list[tuple[dict[str, Any], datetime | None]] = token_rows) -> float:
            return sum(_number(item.get("size_usd")) for item, timestamp in rows
                       if in_window(timestamp, minutes, offset))

        current_5m = volume(5)
        previous_5m = volume(5, 5)
        current_1h = volume(60)
        previous_1h = volume(60, 60)
        token_volume_changes.append({
            "token": token,
            "volume_5m": round(current_5m, 2),
            "volume_1h": round(current_1h, 2),
            "increase_5m": round(current_5m - previous_5m, 2),
            "increase_1h": round(current_1h - previous_1h, 2),
        })
    token_volume_changes.sort(key=lambda item: item["volume_1h"], reverse=True)

    return {
        "recent_count": len(recent),
        "realized_profit": round(sum(_number(item.get("realized_pnl_usd")) for item in recent), 2),
        "token_buys": [
            {"token": token, "count": count, "volume": round(amounts[token], 2),
             "high_accumulation": count > 1}
            for token, count in by_token.most_common()
        ],
        "fomo_tokens": [token for token, amount in amounts.items() if amount >= 50_000],
        "token_volume_changes": token_volume_changes,
        "events": [
            {
                "token": str(item.get("token_symbol") or "UNKNOWN"),
                **classify_trade_event(item.get("side"), item.get("status")),
                "side": item.get("side"), "status": item.get("status"),
                "size_usd": round(_number(item.get("size_usd")), 2),
                "realized_pnl_usd": round(_number(item.get("realized_pnl_usd")), 2),
                "at": timestamp.isoformat() if timestamp is not None else None,
            }
            # Newest first; cap the payload so a busy day stays cheap to ship.
            for item, timestamp in sorted(
                ((item, ts) for item, ts in normalized
                 if in_window(ts, recent_minutes) and ts is not None),
                key=lambda pair: pair[1], reverse=True,
            )[:200]
        ] + [
            # Undated rows (alerts without timestamps) keep a slot at the end.
            {
                "token": str(item.get("token_symbol") or "UNKNOWN"),
                **classify_trade_event(item.get("side"), item.get("status")),
                "side": item.get("side"), "status": item.get("status"),
                "size_usd": round(_number(item.get("size_usd")), 2),
                "realized_pnl_usd": round(_number(item.get("realized_pnl_usd")), 2),
                "at": None,
            }
            for item, timestamp in normalized
            if timestamp is None
        ][:50],
    }
