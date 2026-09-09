from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.schemas.trader import NormalizedBalance, NormalizedTrade, NormalizedTrader


class UnsupportedProviderError(RuntimeError):
    """Raised when a provider capability is not legitimately available."""


class BaseProvider(ABC):
    name = "unknown"

    @abstractmethod
    async def get_leaderboard(self) -> list[NormalizedTrader]: ...

    async def get_trader(self, platform_trader_id: str) -> NormalizedTrader:
        raise UnsupportedProviderError(f"{self.name} does not provide trader profiles")

    async def get_trades(self, platform_trader_id: str) -> list[NormalizedTrade]:
        raise UnsupportedProviderError(f"{self.name} does not provide trades")

    async def get_balances(self, platform_trader_id: str) -> list[NormalizedBalance]:
        raise UnsupportedProviderError(f"{self.name} does not provide balances")

    async def get_alerts(self, limit: int = 50) -> list[dict]:
        raise UnsupportedProviderError(f"{self.name} does not provide alerts")

    def capabilities(self) -> dict[str, bool]:
        return {"leaderboard": True, "user_profile": False, "trades": False, "balances": False, "alerts": False, "realtime": False}

    async def stream_events(self) -> AsyncIterator[NormalizedTrade]:
        raise UnsupportedProviderError(f"{self.name} does not provide realtime events")
        yield  # pragma: no cover
