import asyncio
import logging
import time
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.database.models import Trader
from app.database.repository import TraderRepository
from app.providers.base import BaseProvider
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService

LOGGER = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self, collector: BaseCollector, provider: BaseProvider | None = None,
        detail_limit: int = 50, concurrency: int = 8,
        alert_service: AlertService | None = None, alert_threshold: float = 75,
        confidence_threshold: float = 70,
    ) -> None:
        self.collector = collector
        self.provider = provider or collector.provider
        self.detail_limit = detail_limit
        self.semaphore = asyncio.Semaphore(concurrency)
        self.alert_service = alert_service
        self.alert_threshold = alert_threshold
        self.confidence_threshold = confidence_threshold

    async def _fetch_details(self, item) -> tuple[object | None, list, list, list[str]]:
        errors: list[str] = []
        detail = None
        trades: list = []
        balances: list = []
        async with self.semaphore:
            try:
                if self.provider.capabilities().get("user_profile"):
                    detail = await self.provider.get_trader(item.platform_trader_id)
                if self.provider.capabilities().get("trades"):
                    trades = await self.provider.get_trades(item.platform_trader_id)
                if self.provider.capabilities().get("balances"):
                    balances = await self.provider.get_balances(item.platform_trader_id)
            except Exception as exc:
                errors.append(f"{item.platform_trader_id}: {type(exc).__name__}")
                LOGGER.warning(
                    "Trader detail collection failed provider=%s trader=%s error=%s",
                    self.provider.name, item.platform_trader_id, type(exc).__name__,
                )
        return detail, trades, balances, errors

    async def collect_leaderboard(self, session: AsyncSession) -> int:
        started = datetime.now(UTC)
        timer = time.perf_counter()
        repository = TraderRepository(session)
        run = await repository.start_collection_run(self.provider.name, started)
        errors: list[str] = []
        trades_seen = 0
        try:
            items = await self.collector.collect()
            detail_tasks = []
            for index, item in enumerate(items):
                try:
                    existing = await repository.get_by_platform_id(item.platform, item.platform_trader_id)
                    previous = await repository.get_latest_score(existing.id) if existing else None
                    trader = await repository.upsert_snapshot(item)
                    metrics, result = AnalyticsService.score_snapshot(item)
                    await repository.persist_score(trader.id, metrics, result, item.captured_at)
                    if self.alert_service:
                        await self.alert_service.evaluate_score(
                            session, trader.id, result, previous, item.username, item.rank,
                            self.alert_threshold, self.confidence_threshold,
                        )
                    if index < self.detail_limit:
                        detail_tasks.append((item, trader.id, asyncio.create_task(self._fetch_details(item))))
                except Exception as exc:
                    errors.append(f"{item.platform_trader_id}: {type(exc).__name__}")
            for _item, trader_id, task in detail_tasks:
                detail, trades, balances, detail_errors = await task
                errors.extend(detail_errors)
                trader = await session.get(Trader, trader_id)
                if detail and trader:
                    trader.username = detail.username or trader.username
                    trader.display_name = detail.display_name or trader.display_name
                    trader.wallet_address = detail.wallet_address or trader.wallet_address
                for trade in trades:
                    await repository.persist_trade(trader_id, trade)
                for balance in balances:
                    await repository.persist_balance(trader_id, balance)
                if trades:
                    summary, score = AnalyticsService.score_trades(trades)
                    await repository.persist_score(trader_id, summary.metrics, score, datetime.now(UTC))
                trades_seen += len(trades)
            await repository.finish_collection_run(
                run, datetime.now(UTC), "success" if not errors else "partial",
                len(items), trades_seen, errors, round((time.perf_counter() - timer) * 1000),
            )
            await session.commit()
            return len(items)
        except Exception as exc:
            await session.rollback()
            LOGGER.exception("Collection run failed provider=%s", self.provider.name)
            raise RuntimeError(f"Collection failed: {type(exc).__name__}") from exc
