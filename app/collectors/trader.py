from app.providers.base import BaseProvider
from app.schemas.trader import NormalizedTrader


class TraderCollector:
    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider

    async def collect(self, platform_trader_id: str) -> NormalizedTrader:
        return await self.provider.get_trader(platform_trader_id)
