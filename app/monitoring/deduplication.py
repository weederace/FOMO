from datetime import UTC, datetime, timedelta


class Deduplicator:
    def __init__(self, window_seconds: int = 3600) -> None:
        self.window = timedelta(seconds=window_seconds)
        self._seen: dict[str, datetime] = {}

    async def claim(self, key: str) -> bool:
        now = datetime.now(UTC)
        previous = self._seen.get(key)
        if previous and now - previous < self.window:
            return False
        self._seen[key] = now
        return True


class RedisDeduplicator(Deduplicator):
    """Use Redis atomically when reachable and safely fall back in memory."""

    def __init__(self, redis_url: str, window_seconds: int = 3600) -> None:
        super().__init__(window_seconds)
        self.redis_url = redis_url
        self._redis = None

    async def claim(self, key: str) -> bool:
        try:
            if self._redis is None:
                from redis.asyncio import Redis
                self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            claimed = await self._redis.set(f"fomo:alert:{key}", "1", ex=int(self.window.total_seconds()), nx=True)
            return bool(claimed)
        except Exception:
            return await super().claim(key)
