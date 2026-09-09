from collections.abc import Iterable


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def linear(value: float | None, minimum: float, maximum: float) -> float | None:
    if value is None or maximum <= minimum:
        return None
    return clamp((value - minimum) / (maximum - minimum) * 100)


def weighted_available(metrics: Iterable[tuple[float | None, float]]) -> float | None:
    values = [(value, weight) for value, weight in metrics if value is not None]
    if not values or not sum(weight for _, weight in values):
        return None
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values)
