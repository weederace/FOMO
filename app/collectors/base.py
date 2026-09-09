from abc import ABC, abstractmethod

from app.providers.base import BaseProvider
from app.schemas.trader import NormalizedTrader


class BaseCollector(ABC):
    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider

    @abstractmethod
    async def collect(self) -> list[NormalizedTrader]: ...
