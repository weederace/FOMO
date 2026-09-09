from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class NormalizedTrader(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform: str
    platform_trader_id: str
    username: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    wallet_address: str | None = None
    wallets: dict[str, str] = Field(default_factory=dict)
    rank: int | None = Field(default=None, ge=1)
    pnl: Decimal | None = None
    roi: Decimal | None = None
    volume: Decimal | None = Field(default=None, ge=0)
    follower_count: int | None = Field(default=None, ge=0)
    total_trades: int | None = Field(default=None, ge=0)
    wins: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    captured_at: datetime


class NormalizedTrade(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform: str
    trader_platform_id: str
    platform_trade_id: str | None = None
    token_symbol: str | None = None
    token_address: str | None = None
    chain: str | None = None
    side: str | None = None
    size_usd: Decimal | None = Field(default=None, ge=0)
    price: Decimal | None = Field(default=None, ge=0)
    market_cap: Decimal | None = Field(default=None, ge=0)
    quantity: Decimal | None = Field(default=None, ge=0)
    status: str | None = None
    realized_pnl_usd: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    holding_duration_seconds: int | None = Field(default=None, ge=0)
    transaction_hash: str | None = None
    executed_at: datetime | None = None
    captured_at: datetime


class NormalizedBalance(BaseModel):
    platform: str
    trader_platform_id: str
    token_symbol: str | None = None
    token_address: str | None = None
    chain: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    value_usd: Decimal | None = Field(default=None, ge=0)
    captured_at: datetime
