import asyncio
import logging

LOGGER = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, interval_seconds: int, job) -> None:
        self.interval_seconds, self.job = interval_seconds, job
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.job()
            except Exception:
                LOGGER.exception("Scheduled monitoring job failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            await self._task
