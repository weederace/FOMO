import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.providers.base import BaseProvider
from app.schemas.trader import NormalizedTrade, NormalizedTrader


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self, seed: int = 7, count: int = 25) -> None:
        self._random = random.Random(seed)
        self._traders = self._make_traders(count)

    def capabilities(self) -> dict[str, bool]:
        return {"leaderboard": True, "user_profile": True, "trades": True, "balances": False, "alerts": False, "realtime": False}

    def _make_traders(self, count: int) -> list[NormalizedTrader]:
        now = datetime.now(UTC)
        result = []
        for index in range(count):
            trades = 20 + self._random.randrange(180)
            wins = int(trades * (0.48 + self._random.random() * 0.42))
            result.append(
                NormalizedTrader(
                    platform="mock",
                    platform_trader_id=f"mock-{index + 1}",
                    username=f"whale_{index + 1}",
                    display_name=f"Mock Trader {index + 1}",
                    profile_url=f"https://example.invalid/trader/{index + 1}",
                    rank=index + 1,
                    pnl=Decimal(str(round(self._random.uniform(100, 250000), 2))),
                    roi=Decimal(str(round(self._random.uniform(-10, 240), 2))),
                    volume=Decimal(str(round(self._random.uniform(1000, 5000000), 2))),
                    follower_count=self._random.randrange(10, 100000),
                    total_trades=trades,
                    wins=wins,
                    losses=trades - wins,
                    captured_at=now,
                )
            )
        return result

    async def get_leaderboard(self, window: str = "24h", limit: int | None = None) -> list[NormalizedTrader]:
        return list(self._traders if limit is None else self._traders[:limit])

    async def get_trader(self, platform_trader_id: str) -> NormalizedTrader:
        for trader in self._traders:
            if trader.platform_trader_id == platform_trader_id:
                return trader
        raise KeyError(platform_trader_id)

    async def get_trades(self, platform_trader_id: str) -> list[NormalizedTrade]:
        now = datetime.now(UTC)
        return [
            NormalizedTrade(
                platform="mock",
                trader_platform_id=platform_trader_id,
                platform_trade_id=f"{platform_trader_id}-trade-{i}",
                token_symbol="MOCK",
                side="buy" if i % 2 else "sell",
                size_usd=Decimal("1000"),
                price=Decimal("1.25"),
                executed_at=now - timedelta(hours=i),
                captured_at=now,
            )
            for i in range(1, 6)
        ]
