"""GMGN wallet intelligence: enrich discovered whales with real trading data.

The crawl leaderboard shows a trader's public rank and PnL but no trade
history. GMGN's portfolio endpoints do. This service maps the discovered
wallets to their on-chain address, pulls recent buy/sell activity and current
holdings, and persists them through the ordinary trade/balance pipeline — so
the trade-derived score components (win rate, risk-adjusted, trade quality)
finally have real data to work with.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers.metrics import Metric
from app.config.settings import Settings
from app.database.models import Trader, TraderScore
from app.database.repository import TraderRepository
from app.providers.gmgn import GmgnClient, GmgnProviderError, _to_decimal, _to_timestamp
from app.schemas.trader import NormalizedBalance, NormalizedTrade
from app.services.analytics_service import AnalyticsService

LOGGER = logging.getLogger(__name__)


def _activity_trade(chain: str, wallet: str, row: dict[str, Any]) -> NormalizedTrade | None:
    """Turn one `portfolio activity` row into the internal trade shape."""
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    address = row.get("token_address") or token.get("address") or token.get("contract")
    symbol = token.get("symbol") or row.get("token_symbol") or token.get("name") or row.get("name")
    event_type = str(row.get("event_type") or row.get("type") or row.get("side") or "").lower()
    if event_type in {"buy", "bought", "purchase"}:
        side = "buy"
    elif event_type in {"sell", "sold"}:
        side = "sell"
    else:
        side = event_type or None
    executed_at = _to_timestamp(
        row.get("timestamp") or row.get("block_timestamp") or row.get("time") or row.get("created_at")
    )
    return NormalizedTrade(
        platform="gmgn",
        trader_platform_id=wallet,
        platform_trade_id=str(row.get("tx_hash") or row.get("transaction_hash") or "") or None,
        token_symbol=str(symbol) if symbol else None,
        token_address=str(address) if address else None,
        chain=normalize_chain(chain),
        side=side,
        size_usd=_to_decimal(row.get("cost_usd") or row.get("volume_usd") or row.get("amount_usd")),
        price=_to_decimal(row.get("price") or row.get("price_usd")),
        quantity=_to_decimal(row.get("token_amount") or row.get("amount") or row.get("quantity")),
        realized_pnl_usd=_to_decimal(row.get("realized_profit") or row.get("realized_profit_usd")),
        unrealized_pnl_usd=_to_decimal(row.get("unrealized_profit")),
        status=side,
        transaction_hash=str(row.get("tx_hash") or row.get("transaction_hash") or "") or None,
        executed_at=executed_at,
        captured_at=datetime.now(UTC),
    )


def _holding_balance(chain: str, wallet: str, row: dict[str, Any]) -> NormalizedBalance | None:
    """Turn one `portfolio holdings` row into the internal balance shape."""
    token = row.get("token") if isinstance(row.get("token"), dict) else {}
    address = row.get("token_address") or token.get("address") or token.get("contract")
    symbol = token.get("symbol") or row.get("token_symbol") or token.get("name") or row.get("name")
    return NormalizedBalance(
        platform="gmgn",
        trader_platform_id=wallet,
        token_symbol=str(symbol) if symbol else None,
        token_address=str(address) if address else None,
        chain=normalize_chain(chain),
        amount=_to_decimal(row.get("amount") or row.get("balance") or row.get("token_amount")),
        value_usd=_to_decimal(row.get("usd_value") or row.get("value_usd") or row.get("usd")),
        captured_at=datetime.now(UTC),
    )


def wallet_chain(wallet: str) -> str:
    """Pick the GMGN chain from the address shape: EVM (0x…) vs Solana."""
    return "eth" if str(wallet or "").lower().startswith("0x") else "sol"


def normalize_chain(chain: str | None) -> str | None:
    """Map the CLI chain id to the canonical chain name used everywhere else.

    The gmgn-cli speaks "eth"/"sol", but the price pipeline, radar links and
    GeckoTerminal mapping all key on "solana" — writing the raw CLI name made
    every Solana token invisible to the Token Rankings pricing step.
    """
    return {"eth": "ethereum", "sol": "solana"}.get(str(chain or ""), chain)


def _wallet_candidates(trader: Trader) -> list[str]:
    """Chain-tagged wallets first, then the flat wallet_address fallback.

    FOMO crawl/API rows store both shapes in the `wallets` JSON ("evm" and
    "solana" keys); querying an EVM address against chain=sol is exactly the
    mismatch that made every enrichment run fail before.
    """
    wallets = trader.wallets if isinstance(trader.wallets, dict) else {}
    candidates: list[str] = []
    for key in ("solana", "evm"):
        value = wallets.get(key)
        if value and value not in candidates:
            candidates.append(value)
    if trader.wallet_address and trader.wallet_address not in candidates:
        candidates.append(trader.wallet_address)
    return candidates


async def discover_wallets(session: AsyncSession, settings: Settings) -> list[tuple[int, str]]:
    """Distinct (trader_id, wallet) pairs for the most recently seen whales."""
    traders = list((await session.scalars(
        select(Trader)
        .where(Trader.is_active.is_(True), Trader.wallet_address.is_not(None))
        .order_by(Trader.last_seen_at.desc().nullslast(), Trader.id.desc())
        .limit(settings.gmgn_wallet_limit)
    )).all())
    pairs: list[tuple[int, str]] = []
    seen: set[str] = set()
    for trader in traders:
        for wallet in _wallet_candidates(trader):
            if not wallet or wallet in seen:
                continue
            seen.add(wallet)
            pairs.append((trader.id, wallet))
            break
    return pairs


async def enrich_wallets(session: AsyncSession, settings: Settings, client: GmgnClient | None = None) -> dict[str, int]:
    """Pull real stats/activity/holdings for known whale wallets and persist them.

    Returns a small summary so the worker can log the cycle. Per-wallet errors
    are swallowed: one dead wallet or chain must never stop the run.
    """
    if not settings.gmgn_enabled:
        return {"saved_trades": 0, "saved_balances": 0, "wallets": 0, "errors": 0}
    client = client or GmgnClient(
        command=settings.gmgn_cli_command,
        request_timeout_seconds=settings.gmgn_request_timeout_seconds,
    )
    repository = TraderRepository(session)
    pairs = await discover_wallets(session, settings)
    if not pairs:
        return {"saved_trades": 0, "saved_balances": 0, "wallets": 0, "errors": 0}

    started = datetime.now(UTC)
    run = await repository.start_collection_run("gmgn", started)
    saved_trades = 0
    saved_balances = 0
    errors: list[str] = []

    # Wallets are grouped by chain: the CLI rejects an EVM address on sol (and
    # vice versa), so batches must never mix the two.
    by_chain: dict[str, list[tuple[int, str]]] = {}
    for trader_id, wallet in pairs:
        by_chain.setdefault(wallet_chain(wallet), []).append((trader_id, wallet))

    for chain, chain_pairs in by_chain.items():
        for batch_start in range(0, len(chain_pairs), settings.gmgn_wallet_batch_size):
            batch = chain_pairs[batch_start : batch_start + settings.gmgn_wallet_batch_size]
            wallets = [wallet for _trader_id, wallet in batch]

            # 1) batch stats — cheap, one call for the whole batch
            try:
                await client.portfolio_stats(chain, wallets)
            except GmgnProviderError as exc:
                errors.append(f"stats batch ({chain}): {exc}")
                LOGGER.warning("GMGN stats batch failed chain=%s: %s", chain, exc)
                continue

            # 2) per-wallet activity and holdings
            for trader_id, wallet in batch:
                try:
                    activity = await client.portfolio_activity(
                        chain, wallet, limit=settings.gmgn_activity_limit
                    )
                    for row in activity:
                        trade = _activity_trade(chain, wallet, row)
                        if trade is not None and await repository.persist_trade(trader_id, trade):
                            saved_trades += 1
                    holdings = await client.portfolio_holdings(
                        chain, wallet, limit=settings.gmgn_holdings_limit
                    )
                    for row in holdings:
                        balance = _holding_balance(chain, wallet, row)
                        if balance is not None and await repository.persist_balance(trader_id, balance):
                            saved_balances += 1
                    # Re-score from the trades now on disk so the trade-derived
                    # score components become real instead of missing.
                    await _rescore_from_trades(repository, session, trader_id)
                except GmgnProviderError as exc:
                    errors.append(f"{wallet[:8]}…: {exc}")
                    LOGGER.warning("GMGN wallet enrichment failed wallet=%s error=%s", wallet[:12], exc)
                except Exception:
                    errors.append(f"{wallet[:8]}…: unexpected error")
                    LOGGER.exception("GMGN wallet enrichment crashed wallet=%s", wallet[:12])

    await repository.finish_collection_run(
        run, datetime.now(UTC), "success" if not errors else "partial",
        len(pairs), saved_trades, errors, 0,
    )
    return {
        "saved_trades": saved_trades,
        "saved_balances": saved_balances,
        "wallets": len(pairs),
        "errors": len(errors),
    }


async def _rescore_from_trades(repository: TraderRepository, session: AsyncSession, trader_id: int) -> None:
    """Persist a fresh score computed from the trades stored for this trader."""
    trades = await repository.get_trades(trader_id, limit=200)
    # Trade rows carry no platform column; the trade_key prefix is the marker.
    platform_trades = [trade for trade in trades if (trade.trade_key or "").startswith("gmgn:")]
    if len(platform_trades) < 5:
        return  # not enough evidence yet; keep the existing score
    summary, result = AnalyticsService.score_trades(platform_trades)
    values = {
        name: metric.value
        for name, metric in {
            "consistency": summary.metrics.get("consistency"),
            "win_rate": summary.metrics.get("win_rate"),
            "risk_adjusted": summary.metrics.get("risk_adjusted"),
            "early_entry": summary.metrics.get("early_entry"),
            "trade_quality": summary.metrics.get("trade_quality"),
            "activity": summary.metrics.get("activity"),
        }.items()
        if isinstance(metric, Metric)
    }
    score = TraderScore(
        trader_id=trader_id,
        calculated_at=datetime.now(UTC),
        consistency_score=values.get("consistency"),
        win_rate_score=values.get("win_rate"),
        risk_adjusted_score=values.get("risk_adjusted"),
        early_entry_score=values.get("early_entry"),
        trade_quality_score=values.get("trade_quality"),
        activity_score=values.get("activity"),
        whale_score=result.score,
        score_confidence=result.confidence,
        classification=result.classification,
    )
    session.add(score)
    await session.flush()


def wallet_score(stats) -> dict[str, Any]:
    """Simple track-record read-out in the spirit of GMGN's Address Score.

    Profitability is judged by outcome, not raw win rate: realized PnL first,
    then the PnL multiplier, then win rate as a tiebreaker.
    """
    realized = float(stats.realized_profit_usd) if stats.realized_profit_usd is not None else None
    win_rate = stats.win_rate
    if win_rate is not None and win_rate <= 1:
        win_rate *= 100  # the CLI reports 0..1 in some payloads and 0..100 in others

    if realized is None and win_rate is None:
        score = None
        verdict = "UNKNOWN — no GMGN stats for this wallet"
    elif (realized or 0) > 0 and (win_rate is None or win_rate >= 50):
        score = 85 if (stats.pnl_multiplier or 0) >= 2 else 75
        verdict = "PROVEN — positive realized PnL"
    elif (realized or 0) > 0:
        score = 60
        verdict = "GREEN BUT RISKY — positive PnL, sub-50% win rate"
    elif realized is not None and realized < 0:
        score = 25
        verdict = "LOSING — negative realized PnL"
    else:
        score = 50
        verdict = "MIXED — small or flat realized PnL"

    style = "unknown"
    buys, sells = stats.buy_count or 0, stats.sell_count or 0
    if buys + sells >= 100:
        style = "high-frequency"
    elif (stats.total_spent_usd or Decimal(0)) > Decimal(100_000):
        style = "large-size"
    elif 0 < buys + sells < 30:
        style = "low-frequency"

    return {
        "score": score,
        "verdict": verdict,
        "style": style,
        "realized_profit_usd": float(realized) if realized is not None else None,
        "win_rate": win_rate,
        "pnl_multiplier": stats.pnl_multiplier,
        "buy_count": buys or None,
        "sell_count": sells or None,
    }
