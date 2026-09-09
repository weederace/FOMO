from dataclasses import dataclass
from statistics import pstdev
from typing import Any

from app.analyzers.normalization import clamp


@dataclass(frozen=True)
class Metric:
    value: float | None
    confidence: float
    sufficient: bool
    reason: str


@dataclass(frozen=True)
class TradeSummary:
    total_trades: int
    winning_trades: int | None
    losing_trades: int | None
    total_pnl: float | None
    average_pnl: float | None
    median_pnl: float | None
    profit_factor: float | None
    average_trade_size: float | None
    trading_volume: float | None
    average_holding_time: float | None
    best_trade: float | None
    worst_trade: float | None
    data_window_days: int | None
    metrics: dict[str, Metric]


def win_rate(wins: int | None, total: int | None) -> Metric:
    if wins is None or total is None or total < 5:
        return Metric(None, 0, False, "At least 5 observed trades are required")
    value = wins / total
    return Metric(value, clamp(total / 100 * 100), True, f"Based on {total} observed trades")


def consistency(period_returns: list[float]) -> Metric:
    if len(period_returns) < 3:
        return Metric(None, 0, False, "At least 3 observations are required")
    positive = sum(item > 0 for item in period_returns) / len(period_returns)
    volatility = pstdev(period_returns) if len(period_returns) > 1 else 0
    score = clamp(positive * 100 - min(volatility * 10, 50))
    return Metric(
        score,
        clamp(len(period_returns) / 30 * 100),
        True,
        f"Based on {len(period_returns)} periods",
    )


def activity(active_days: int | None, freshness_days: float | None) -> Metric:
    if active_days is None or freshness_days is None:
        return Metric(None, 0, False, "Activity history unavailable")
    return Metric(
        clamp(active_days / 30 * 70 + max(0, 30 - freshness_days)),
        80,
        True,
        "Based on activity and freshness",
    )


def rank_momentum(previous: int | None, current: int | None) -> Metric:
    if previous is None or current is None or previous <= 0:
        return Metric(None, 0, False, "Two valid rank observations are required")
    movement = previous - current
    return Metric(movement, 70, True, f"Rank changed from {previous} to {current}")


def calculate_trade_summary(trades: list[Any]) -> TradeSummary:
    pnls = [float(trade.realized_pnl_usd) for trade in trades if trade.realized_pnl_usd is not None]
    sizes = [float(trade.size_usd) for trade in trades if trade.size_usd is not None]
    holds = [float(trade.holding_duration_seconds) for trade in trades if trade.holding_duration_seconds is not None]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    dates = [trade.executed_at for trade in trades if trade.executed_at is not None]
    window_days = (max(dates) - min(dates)).days if len(dates) >= 2 else None
    sample = len(pnls)
    metrics: dict[str, Metric] = {}
    if sample >= 5:
        metrics["win_rate"] = Metric(
            len(wins) / sample * 100, min(100, sample), True,
            f"Based on {sample} realized trades",
        )
        average_win = sum(wins) / max(len(wins), 1)
        average_loss = abs(sum(losses) / max(len(losses), 1))
        metrics["trade_quality"] = Metric(
            clamp(average_win / (average_loss + 1) * 50), min(100, sample), True,
            "Based on realized win/loss quality",
        )
        if gross_loss:
            metrics["risk_adjusted"] = Metric(
                clamp(gross_profit / gross_loss * 40), min(100, sample), True,
                "Based on profit factor",
            )
        if window_days is not None:
            metrics["consistency"] = consistency(pnls)
    if trades:
        metrics["activity"] = Metric(
            clamp(len(trades) / 30 * 100), min(100, len(trades) * 5), True,
            "Based on observed trade count",
        )
    return TradeSummary(
        total_trades=len(trades), winning_trades=len(wins) if pnls else None,
        losing_trades=len(losses) if pnls else None, total_pnl=sum(pnls) if pnls else None,
        average_pnl=sum(pnls) / sample if sample else None,
        median_pnl=sorted(pnls)[sample // 2] if sample else None,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        average_trade_size=sum(sizes) / len(sizes) if sizes else None,
        trading_volume=sum(sizes) if sizes else None,
        average_holding_time=sum(holds) / len(holds) if holds else None,
        best_trade=max(pnls) if pnls else None, worst_trade=min(pnls) if pnls else None,
        data_window_days=window_days, metrics=metrics,
    )
