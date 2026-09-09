from dataclasses import dataclass

from app.analyzers.metrics import Metric
from app.analyzers.normalization import clamp, weighted_available

WEIGHTS = {
    "consistency": 0.25,
    "win_rate": 0.15,
    "risk_adjusted": 0.20,
    "early_entry": 0.15,
    "trade_quality": 0.15,
    "activity": 0.10,
}


@dataclass(frozen=True)
class WhaleScore:
    score: float | None
    confidence: float
    classification: str
    reasoning: str = ""


def classify(score: float | None) -> str:
    if score is None:
        return "LOW SIGNAL"
    if score < 30:
        return "LOW SIGNAL"
    if score < 50:
        return "WATCHLIST"
    if score < 70:
        return "PROMISING"
    if score < 85:
        return "SMART WHALE"
    return "ELITE WHALE"


def calculate(
    metrics: dict[str, Metric], observed_trades: int = 0, history_days: int = 0
) -> WhaleScore:
    parts = [
        (metric.value, WEIGHTS[name])
        for name, metric in metrics.items()
        if name in WEIGHTS and metric.sufficient and metric.value is not None
    ]
    score = weighted_available(parts)
    if score is None:
        return WhaleScore(None, 0, "LOW SIGNAL", "No sufficiently supported metrics")
    completeness = sum(weight for _, weight in parts) / sum(WEIGHTS.values())
    confidence = clamp(
        completeness * 45 + min(observed_trades / 100, 1) * 30 + min(history_days / 30, 1) * 25
    )
    names = ", ".join(name for name, metric in metrics.items() if metric.sufficient and metric.value is not None)
    return WhaleScore(score, confidence, classify(score), f"Based on available metrics: {names}")
