from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers.metrics import Metric, calculate_trade_summary, win_rate
from app.analyzers.whale_score import WhaleScore, calculate
from app.database.repository import TraderRepository
from app.schemas.trader import NormalizedTrader


class AnalyticsService:
    @staticmethod
    def score(metrics, observed_trades: int = 0, history_days: int = 0):
        return calculate(metrics, observed_trades, history_days)

    @staticmethod
    def score_snapshot(item: NormalizedTrader) -> tuple[dict[str, Metric], WhaleScore]:
        win_rate_metric = win_rate(item.wins, item.total_trades)
        if win_rate_metric.value is not None:
            win_rate_metric = Metric(
                win_rate_metric.value * 100,
                win_rate_metric.confidence,
                win_rate_metric.sufficient,
                win_rate_metric.reason,
            )
        metrics = {"win_rate": win_rate_metric}
        return metrics, calculate(metrics, item.total_trades or 0)

    @staticmethod
    def score_trades(trades) -> tuple[object, WhaleScore]:
        summary = calculate_trade_summary(trades)
        return summary, calculate(summary.metrics, summary.total_trades, summary.data_window_days or 0)

    @staticmethod
    async def persist_snapshot_score(
        session: AsyncSession, item: NormalizedTrader, calculated_at: datetime | None = None
    ) -> WhaleScore:
        repository = TraderRepository(session)
        trader = await repository.upsert_snapshot(item)
        metrics, result = AnalyticsService.score_snapshot(item)
        await repository.persist_score(trader.id, metrics, result, calculated_at)
        return result
