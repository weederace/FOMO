from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Alert
from app.monitoring.deduplication import Deduplicator
from app.telegram.formatter import format_alert


class AlertService:
    def __init__(self, deduplicator: Deduplicator | None = None, notifier=None) -> None:
        self.deduplicator = deduplicator or Deduplicator()
        self.notifier = notifier

    async def claim_event(self, event_key: str) -> bool:
        return await self.deduplicator.claim(event_key)

    async def create_alert(
        self, session: AsyncSession, trader_id: int | None, alert_type: str,
        payload: dict, event_id: str,
    ) -> Alert | None:
        key = f"{alert_type}:{trader_id}:{event_id}"
        if not await self.claim_event(key):
            return None
        existing = await session.scalar(select(Alert).where(Alert.event_key == key))
        if existing:
            return None
        alert = Alert(trader_id=trader_id, alert_type=alert_type, event_key=key, payload=payload)
        session.add(alert)
        await session.flush()
        if self.notifier:
            username = payload.get("username") or payload.get("trader", {}).get("username", "unknown")
            message = format_alert(
                alert_type, username, payload.get("whale_score"), payload.get("confidence", payload.get("score_confidence")),
                payload.get("classification"), payload.get("rank"),
            )
            if await self.notifier.send(message):
                alert.sent_at = datetime.now(UTC)
        return alert

    async def evaluate_score(self, session: AsyncSession, trader_id: int, score, previous, username: str | None, rank: int | None, threshold: float = 75, confidence_threshold: float = 70) -> Alert | None:
        if score.score is None or score.confidence < confidence_threshold:
            return None
        if score.score < threshold or (previous and previous.whale_score is not None and float(previous.whale_score) >= threshold):
            return None
        return await self.create_alert(
            session, trader_id, "NEW_SMART_WHALE",
            {"username": username, "whale_score": score.score, "confidence": score.confidence, "classification": score.classification, "rank": rank, "source": "FOMOAPI"},
            str(datetime.now(UTC).timestamp()),
        )

    async def create_provider_alert(self, session: AsyncSession, payload: dict, event_id: str | None = None) -> Alert | None:
        event_key = event_id or payload.get("eventId") or payload.get("id") or str(payload)
        alert_type = f"{payload.get('type', 'ALERT')}".upper() if payload.get("type", "").startswith("GMGN_") else f"FOMO_{str(payload.get('type', 'ALERT')).upper()}"
        return await self.create_alert(session, None, alert_type, payload, event_key)
