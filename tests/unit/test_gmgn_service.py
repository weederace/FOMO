"""Tests for GMGN wallet enrichment persistence (in-memory SQLite)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.gmgn_service as gmgn_service_module
from app.config.settings import Settings
from app.database.models import BalanceSnapshot, Base, CollectionRun, Trade, Trader
from app.database.repository import TraderRepository
from app.schemas.trader import NormalizedTrader
from app.services.gmgn_service import (
    _activity_trade,
    _holding_balance,
    discover_wallets,
    enrich_wallets,
)


class FakeGmgnClient:
    """Stands in for GmgnClient; replays canned activity/holdings per wallet."""

    def __init__(self, activity=None, holdings=None) -> None:
        self.activity = activity or {}
        self.holdings = holdings or {}
        self.activity_calls: list[str] = []
        self.holdings_calls: list[str] = []
        self.stats_calls: list[list[str]] = []

    async def portfolio_stats(self, chain: str, wallets: list[str], period: str = "30d"):
        self.stats_calls.append(list(wallets))
        return []

    async def portfolio_activity(self, chain: str, wallet: str, limit: int = 50, types=("buy", "sell")):
        self.activity_calls.append(wallet)
        return self.activity.get(wallet, [])

    async def portfolio_holdings(self, chain: str, wallet: str, limit: int = 50):
        self.holdings_calls.append(wallet)
        return self.holdings.get(wallet, [])


def make_settings(**overrides) -> Settings:
    values = {
        "gmgn_enabled": True,
        "gmgn_default_chain": "sol",
        "gmgn_wallet_limit": 10,
        "gmgn_wallet_batch_size": 2,
        "gmgn_activity_limit": 50,
        "gmgn_holdings_limit": 50,
    }
    values.update(overrides)
    return Settings(database_url="sqlite+aiosqlite:///:memory:", **values)


def activity_row(tx: str, event: str, symbol: str, hours_ago: int) -> dict:
    return {
        "tx_hash": tx,
        "event_type": event,
        "token": {"symbol": symbol, "address": f"mint-{symbol.lower()}"},
        "cost_usd": "1500.25",
        "price": "0.42",
        "token_amount": "3572.0",
        "timestamp": int((datetime.now(UTC) - timedelta(hours=hours_ago)).timestamp()),
    }


def holding_row(symbol: str, amount: str, usd: str) -> dict:
    return {
        "token": {"symbol": symbol, "address": f"mint-{symbol.lower()}"},
        "amount": amount,
        "usd_value": usd,
    }


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def seed_trader(factory, platform_trader_id: str, wallet: str) -> Trader:
    async with factory() as session:
        trader = await TraderRepository(session).upsert_snapshot(
            NormalizedTrader(
                platform="fomo",
                platform_trader_id=platform_trader_id,
                username=platform_trader_id,
                wallet_address=wallet,
                captured_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return trader


async def test_activity_row_maps_buy_and_sell():
    trade = _activity_trade("sol", "wallet1", activity_row("tx1", "buy", "WIF", 3))
    assert trade.platform == "gmgn"
    assert trade.trader_platform_id == "wallet1"
    assert trade.side == "buy" and trade.status == "buy"
    assert trade.size_usd == Decimal("1500.25")
    assert trade.token_address == "mint-wif"
    assert trade.executed_at is not None and trade.executed_at.tzinfo is not None

    sell = _activity_trade("sol", "wallet1", {"event_type": "SELL", "token_symbol": "BONK"})
    assert sell.side == "sell" and sell.token_symbol == "BONK"

    # Rows with only a token *name* (no symbol field) still resolve a symbol.
    named = _activity_trade("sol", "wallet1", {"event_type": "buy", "token": {"name": "Treecoin"}})
    assert named.token_symbol == "Treecoin"

    unknown = _activity_trade("sol", "wallet1", {})
    assert unknown.side is None and unknown.token_symbol is None


async def test_holding_row_maps_balance():
    balance = _holding_balance("sol", "wallet1", holding_row("WIF", "100", "2500.5"))
    assert balance.token_symbol == "WIF"
    assert balance.amount == Decimal("100")
    assert balance.value_usd == Decimal("2500.5")
    assert balance.captured_at.tzinfo is not None


async def test_discover_wallets_returns_distinct_recent_pairs(db):
    factory = db
    await seed_trader(factory, "t1", "walletA")
    await seed_trader(factory, "t2", "walletB")
    async with factory() as session:
        # a trader without a wallet must be skipped
        await TraderRepository(session).upsert_snapshot(
            NormalizedTrader(platform="fomo", platform_trader_id="t3", captured_at=datetime.now(UTC))
        )
        await session.commit()

    async with factory() as session:
        settings = make_settings()
        pairs = await discover_wallets(session, settings)

    assert sorted(wallet for _trader, wallet in pairs) == ["walletA", "walletB"]


async def test_discover_wallets_prefers_chain_tagged_solana_wallet(db):
    """The flat wallet_address is often the EVM one; when the wallets JSON
    carries a Solana address it must win so the CLI is never queried with a
    0x… address against chain=sol."""
    factory = db
    async with factory() as session:
        await TraderRepository(session).upsert_snapshot(
            NormalizedTrader(
                platform="fomo", platform_trader_id="mixed", username="mixed",
                wallet_address="0xabc123",
                wallets={"evm": "0xabc123", "solana": "So1Wallet11111111111111111111111111111111"},
                captured_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with factory() as session:
        pairs = await discover_wallets(session, make_settings())

    assert pairs == [(pairs[0][0], "So1Wallet11111111111111111111111111111111")]


def test_wallet_chain_from_address_shape():
    from app.services.gmgn_service import wallet_chain

    assert wallet_chain("0xabc123") == "eth"
    assert wallet_chain("So1Wallet11111111111111111111111111111111") == "sol"
    assert wallet_chain("") == "sol"


def test_normalize_chain_maps_cli_ids_to_canonical_names():
    """Persisted trades must carry "solana"/"ethereum" — the GeckoTerminal
    pricing pipeline and every radar link key on those names, not the CLI's
    "sol"/"eth" ids."""
    from app.services.gmgn_service import normalize_chain

    assert normalize_chain("sol") == "solana"
    assert normalize_chain("eth") == "ethereum"
    assert normalize_chain("base") == "base"
    assert normalize_chain(None) is None


async def test_activity_trade_persists_canonical_chain(db):
    """A solana activity row written via the CLI chain id must land as
    chain='solana' in the trades table."""
    from sqlalchemy import select

    from app.database.models import Trade
    from app.database.repository import TraderRepository
    from app.services.gmgn_service import _activity_trade

    factory = db
    await seed_trader(factory, "chain_canon", "So1Wallet11111111111111111111111111111111")
    trade = _activity_trade("sol", "So1Wallet11111111111111111111111111111111", {
        "event_type": "buy", "token": {"symbol": "WIF", "address": "Ekp9CSsBf"},
        "cost_usd": "100", "timestamp": "2026-01-01T00:00:00Z",
        "tx_hash": "tx-canonical-1",
    })
    async with factory() as session:
        trader = await TraderRepository(session).get_by_platform_id("fomo", "chain_canon")
        await TraderRepository(session).persist_trade(trader.id, trade)
        await session.commit()
    async with factory() as session:
        saved = (await session.scalars(select(Trade).where(Trade.platform_trade_id == "tx-canonical-1"))).one()
    assert saved.chain == "solana"


async def test_enrich_wallets_groups_batches_by_chain(db):
    """EVM and Solana wallets must land in separate CLI calls with the right
    chain per call — one mixed batch is exactly what broke enrichment."""
    factory = db
    await seed_trader(factory, "evm_whale", "0xdeadbeef")
    await seed_trader(factory, "sol_whale", "So1Wallet11111111111111111111111111111111")
    client = FakeGmgnClient()

    async with factory() as session:
        summary = await enrich_wallets(session, make_settings(), client)
        await session.commit()

    assert summary["errors"] == 0
    # every stats call must be chain-homogeneous: recorded via calls per wallet
    touched = set(client.activity_calls) | {w for call in client.stats_calls for w in call}
    assert "0xdeadbeef" in touched and "So1Wallet11111111111111111111111111111111" in touched


async def test_enrich_wallets_persists_trades_and_balances(db):
    factory = db
    await seed_trader(factory, "t1", "walletA")
    client = FakeGmgnClient(
        activity={"walletA": [activity_row("tx1", "buy", "WIF", 2), activity_row("tx2", "sell", "WIF", 1)]},
        holdings={"walletA": [holding_row("WIF", "100", "2500.5"), holding_row("BONK", "5", "12")]},
    )

    async with factory() as session:
        summary = await enrich_wallets(session, make_settings(), client)
        await session.commit()

    assert summary["wallets"] == 1
    assert summary["saved_trades"] == 2
    assert summary["saved_balances"] == 2
    assert summary["errors"] == 0

    async with factory() as session:
        trades = list((await session.scalars(select(Trade))).all())
        balances = list((await session.scalars(select(BalanceSnapshot))).all())
        runs = list((await session.scalars(select(CollectionRun))).all())
    assert {trade.side for trade in trades} == {"buy", "sell"}
    assert all(trade.trade_key for trade in trades)
    assert len(balances) == 2
    assert len(runs) == 1 and runs[0].status == "success" and runs[0].provider == "gmgn"


async def test_enrich_wallets_deduplicates_on_second_run(db):
    factory = db
    await seed_trader(factory, "t1", "walletA")
    client = FakeGmgnClient(activity={"walletA": [activity_row("tx1", "buy", "WIF", 2)]})

    async with factory() as session:
        first = await enrich_wallets(session, make_settings(), client)
        await session.commit()
    async with factory() as session:
        second = await enrich_wallets(session, make_settings(), client)
        await session.commit()

    assert first["saved_trades"] == 1
    assert second["saved_trades"] == 0  # same tx_hash → dedup via trade_key


async def test_enrich_wallets_survives_wallet_errors(db):
    factory = db
    await seed_trader(factory, "t1", "walletA")
    await seed_trader(factory, "t2", "walletB")

    class FailingClient(FakeGmgnClient):
        async def portfolio_activity(self, chain, wallet, limit=50, types=("buy", "sell")):
            if wallet == "walletA":
                raise Exception("cli exploded")
            return []

    async with factory() as session:
        summary = await enrich_wallets(session, make_settings(), FailingClient())
        await session.commit()

    assert summary["wallets"] == 2
    assert summary["errors"] == 1  # walletA failed, walletB still processed


async def test_enrich_wallets_noop_when_disabled(db):
    factory = db
    await seed_trader(factory, "t1", "walletA")
    client = FakeGmgnClient(activity={"walletA": [activity_row("tx1", "buy", "WIF", 2)]})

    async with factory() as session:
        summary = await enrich_wallets(session, make_settings(gmgn_enabled=False), client)
        await session.commit()

    assert summary == {"saved_trades": 0, "saved_balances": 0, "wallets": 0, "errors": 0}
    assert client.activity_calls == []  # the CLI was never invoked


async def test_rescore_runs_after_enough_gmgn_trades(db, monkeypatch):
    factory = db
    trader = await seed_trader(factory, "t1", "walletA")
    rows = [activity_row(f"tx{i}", "buy" if i % 2 else "sell", "WIF", i) for i in range(6)]
    client = FakeGmgnClient(activity={"walletA": rows})

    scored: list[int] = []
    async def fake_rescore(repository, session, trader_id):
        scored.append(trader_id)

    monkeypatch.setattr(gmgn_service_module, "_rescore_from_trades", fake_rescore)

    async with factory() as session:
        await enrich_wallets(session, make_settings(), client)
        await session.commit()

    assert scored == [trader.id]
