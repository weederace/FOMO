from app.collectors.base import BaseCollector
from app.schemas.trader import NormalizedTrader


class MockCollector(BaseCollector):
    async def collect(self) -> list[NormalizedTrader]:
        return await self.provider.get_leaderboard()
