from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Trader(Base):
    __tablename__ = "traders"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(50))
    platform_trader_id: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(Text)
    wallet_address: Mapped[str | None] = mapped_column(String(255))
    wallets: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(default=True)
    snapshots: Mapped[list["TraderSnapshot"]] = relationship(cascade="all, delete-orphan")
    scores: Mapped[list["TraderScore"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("platform", "platform_trader_id"),)


class TraderSnapshot(Base):
    __tablename__ = "trader_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rank: Mapped[int | None]
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    roi: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    follower_count: Mapped[int | None]
    total_trades: Mapped[int | None]
    wins: Mapped[int | None]
    losses: Mapped[int | None]
    source: Mapped[str | None] = mapped_column(String(50), default="unknown")
    __table_args__ = (
        UniqueConstraint("trader_id", "captured_at"),
        Index("ix_trader_snapshots_trader_captured", "trader_id", "captured_at"),
        Index("ix_trader_snapshots_rank_captured", "rank", "captured_at"),
    )


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    platform_trade_id: Mapped[str | None] = mapped_column(String(255))
    token_symbol: Mapped[str | None] = mapped_column(String(100))
    token_address: Mapped[str | None] = mapped_column(String(255))
    chain: Mapped[str | None] = mapped_column(String(100))
    side: Mapped[str | None] = mapped_column(String(20))
    size_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    status: Mapped[str | None] = mapped_column(String(20))
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    unrealized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    holding_duration_seconds: Mapped[int | None]
    transaction_hash: Mapped[str | None] = mapped_column(String(255))
    trade_key: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("trader_id", "platform_trade_id"),
        Index("ix_trades_trader_executed", "trader_id", "executed_at"),
        Index("uq_trades_trade_key", "trade_key", unique=True),
    )


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    token_symbol: Mapped[str | None] = mapped_column(String(100))
    token_address: Mapped[str | None] = mapped_column(String(255))
    chain: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    value_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    balance_key: Mapped[str] = mapped_column(String(600), unique=True)
    __table_args__ = (Index("ix_balances_trader_captured", "trader_id", "captured_at"),)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    traders_seen: Mapped[int] = mapped_column(default=0)
    trades_seen: Mapped[int] = mapped_column(default=0)
    errors: Mapped[list | None] = mapped_column(JSON)
    duration_ms: Mapped[int | None]


class TraderScore(Base):
    __tablename__ = "trader_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int] = mapped_column(ForeignKey("traders.id", ondelete="CASCADE"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consistency_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    win_rate_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    risk_adjusted_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    early_entry_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    trade_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    activity_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    whale_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    score_confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    classification: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (Index("ix_trader_scores_trader_calculated", "trader_id", "calculated_at"),)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    trader_id: Mapped[int | None] = mapped_column(ForeignKey("traders.id", ondelete="SET NULL"))
    alert_type: Mapped[str] = mapped_column(String(80))
    event_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WatchlistItem(Base):
    """A token the user pinned for 5-minute re-checks.

    Rows are keyed by (chain, address); the latest snapshot of each is cached
    into `payload` whenever the worker refreshes the watchlist so the UI can
    render instantly even between refreshes.
    """

    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(primary_key=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_watchlist_chain_address"),)


AlertEvent = Alert
