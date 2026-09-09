from app.config.settings import Settings


class MonitoringService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_watchlisted(self, score: float | None, confidence: float | None) -> bool:
        return (
            score is not None
            and confidence is not None
            and score >= self.settings.watchlist_score_threshold
            and confidence >= self.settings.watchlist_confidence_threshold
        )
