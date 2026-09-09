from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EmergingStatus(StrEnum):
    NOT_EMERGING = "NOT_EMERGING"
    POSSIBLY_EMERGING = "POSSIBLY_EMERGING"
    EMERGING = "EMERGING"
    STRONGLY_EMERGING = "STRONGLY_EMERGING"


@dataclass(frozen=True)
class EmergingConfig:
    min_history_points: int = 3
    min_score: float = 60
    min_confidence: float = 60
    min_score_growth: float = 10
    lookback_days: int = 7


@dataclass(frozen=True)
class EmergingAnalysis:
    status: EmergingStatus
    score_change: float | None
    confidence_change: float | None
    rank_change: int | None
    rank_velocity: float | None
    history_points: int
    lookback_days: int
    reason: str


def _not_emerging(points: int, config: EmergingConfig, reason: str) -> EmergingAnalysis:
    return EmergingAnalysis(
        EmergingStatus.NOT_EMERGING,
        None,
        None,
        None,
        None,
        points,
        config.lookback_days,
        reason,
    )


def analyze_history(
    scores: list[tuple[datetime, float, float]],
    ranks: list[tuple[datetime, int | None]],
    config: EmergingConfig | None = None,
) -> EmergingAnalysis:
    config = config or EmergingConfig()
    if len(scores) < config.min_history_points:
        return _not_emerging(
            len(scores), config, "Insufficient historical score observations"
        )

    scores = sorted(scores, key=lambda item: item[0])
    first_time, first_score, first_confidence = scores[0]
    last_time, last_score, last_confidence = scores[-1]
    score_change = last_score - first_score
    confidence_change = last_confidence - first_confidence
    transitions = [
        current[1] - previous[1] for previous, current in zip(scores, scores[1:], strict=False)
    ]
    positive_steps = sum(delta > 0 for delta in transitions)
    largest_drawdown = max(
        (max(scores[index][1] - scores[later][1], 0) for index in range(len(scores)) for later in range(index + 1, len(scores))),
        default=0,
    )
    if last_score < config.min_score:
        return _not_emerging(len(scores), config, "Current score is below the emerging threshold")
    if last_confidence < config.min_confidence:
        return _not_emerging(len(scores), config, "Current score confidence is below the emerging threshold")
    if score_change < config.min_score_growth:
        return _not_emerging(len(scores), config, "Score growth is below the emerging threshold")
    if largest_drawdown > max(config.min_score_growth, 20):
        return _not_emerging(len(scores), config, "Score history contains a material reversal")

    valid_ranks = [(timestamp, rank) for timestamp, rank in ranks if rank is not None]
    valid_ranks.sort(key=lambda item: item[0])
    rank_change = None
    rank_velocity = None
    rank_positive_steps = 0
    if len(valid_ranks) >= 2:
        rank_change = valid_ranks[0][1] - valid_ranks[-1][1]
        elapsed_days = max((valid_ranks[-1][0] - valid_ranks[0][0]).total_seconds() / 86400, 1 / 24)
        rank_velocity = rank_change / elapsed_days
        rank_positive_steps = sum(
            previous > current
            for (_, previous), (_, current) in zip(valid_ranks, valid_ranks[1:], strict=False)
        )

    if positive_steps >= len(transitions) and confidence_change >= 0 and rank_change and rank_positive_steps >= 2:
        status = EmergingStatus.STRONGLY_EMERGING
        reason = "Sustained score improvement, increasing confidence, and strong positive rank momentum across multiple observations."
    elif positive_steps / len(transitions) >= 0.67 and (rank_change or 0) > 0:
        status = EmergingStatus.EMERGING
        reason = "Score improved across most observations with positive rank movement."
    else:
        status = EmergingStatus.POSSIBLY_EMERGING
        reason = "Score growth meets the threshold, but sustained rank or score momentum needs confirmation."
    return EmergingAnalysis(
        status,
        score_change,
        confidence_change,
        rank_change,
        rank_velocity,
        len(scores),
        max(0, (last_time - first_time).days),
        reason,
    )


def is_emerging(rank_history: list[int], score_history: list[float], activity_growth: float = 0) -> bool:
    """Compatibility helper for callers that only have in-memory scalar history."""
    if len(rank_history) < 3 or len(score_history) < 3 or activity_growth < 0:
        return False
    rank_improving = rank_history[-1] < rank_history[0] and sum(
        a > b for a, b in zip(rank_history, rank_history[1:], strict=False)
    ) >= 2
    score_improving = score_history[-1] > score_history[0] and score_history[-1] >= 60
    return rank_improving and score_improving
