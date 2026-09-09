from types import SimpleNamespace

from app.analyzers.metrics import calculate_trade_summary
from app.analyzers.whale_score import classify


def test_trade_summary_requires_realized_sample_for_performance_metrics():
    trades = [SimpleNamespace(realized_pnl_usd=value, size_usd=100, holding_duration_seconds=60, executed_at=None) for value in (10, -5, 20, -2, 8)]
    summary = calculate_trade_summary(trades)
    assert summary.total_trades == 5
    assert summary.winning_trades == 3
    assert summary.profit_factor is not None
    assert "win_rate" in summary.metrics


def test_updated_score_classification_thresholds():
    assert classify(29) == "LOW SIGNAL"
    assert classify(30) == "WATCHLIST"
    assert classify(70) == "SMART WHALE"
    assert classify(85) == "ELITE WHALE"
