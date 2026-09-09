from app.providers.base import BaseProvider


class RealtimeCollector:
    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider

    async def collect(self):
        return self.provider.stream_events()
