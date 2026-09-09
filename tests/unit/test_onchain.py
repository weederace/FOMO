import asyncio

import pytest

from app.providers.quota import QuotaExceeded, RequestQuota


def test_quota_stops_at_window_limit():
    async def run():
        quota = RequestQuota(per_second=10_000, window_seconds=60, window_limit=2)
        await quota.acquire()
        await quota.acquire()
        with pytest.raises(QuotaExceeded):
            await quota.acquire()

    asyncio.run(run())
