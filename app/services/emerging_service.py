from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers.emerging import EmergingConfig, EmergingStatus, analyze_history
from app.config.settings import Settings
from app.database.models import Trader
from app.database.repository import TraderRepository


async def find_emerging_traders(
    session: AsyncSession, settings: Settings, limit: int = 50
) -> list[dict]:
    now = datetime.now(UTC)
    start = now - timedelta(days=settings.emerging_lookback_days)
    repository = TraderRepository(session)
    traders = list((await session.scalars(select(Trader).order_by(Trader.id))).all())
    results: list[dict] = []
    config = EmergingConfig(
        min_history_points=settings.min_history_points,
        min_score=settings.emerging_min_score,
        min_confidence=settings.emerging_min_confidence,
        min_score_growth=settings.emerging_min_score_growth,
        lookback_days=settings.emerging_lookback_days,
    )
    for trader in traders:
        scores = await repository.get_score_history_between(trader.id, start, now)
        if not scores:
            continue
        snapshots = await repository.get_snapshot_history(trader.id, start)
        analysis = analyze_history(
            [
                (
                    score.calculated_at,
                    float(score.whale_score),
                    float(score.score_confidence),
                )
                for score in scores
                if score.whale_score is not None and score.score_confidence is not None
            ],
            [(snapshot.captured_at, snapshot.rank) for snapshot in snapshots],
            config,
        )
        if analysis.status in {EmergingStatus.NOT_EMERGING, EmergingStatus.POSSIBLY_EMERGING}:
            continue
        current = max(scores, key=lambda score: (score.calculated_at, score.id))
        results.append(
            {
                "trader": {
                    "id": trader.id,
                    "platform": trader.platform,
                    "platform_trader_id": trader.platform_trader_id,
                    "username": trader.username,
                    "display_name": trader.display_name,
                    "profile_url": trader.profile_url,
                },
                "whale_score": float(current.whale_score) if current.whale_score is not None else None,
                "score_confidence": float(current.score_confidence)
                if current.score_confidence is not None
                else None,
                "classification": current.classification,
                "emerging_status": analysis.status,
                "score_change": analysis.score_change,
                "confidence_change": analysis.confidence_change,
                "rank_change": analysis.rank_change,
                "rank_velocity": analysis.rank_velocity,
                "lookback_days": analysis.lookback_days,
                "history_points": analysis.history_points,
                "data_freshness": trader.last_seen_at,
                "reason": analysis.reason,
            }
        )
    results.sort(key=lambda item: item["whale_score"] or 0, reverse=True)
    return results[:limit]
