from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analyzers.emerging import EmergingConfig, EmergingStatus, analyze_history
from app.analyzers.metrics import Metric
from app.analyzers.whale_score import WhaleScore
from app.database.models import Base
from app.database.repository import TraderRepository
from app.schemas.trader import NormalizedTrader


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def score(value: float, confidence: float) -> WhaleScore:
    return WhaleScore(value, confidence, "SMART WHALE")


@pytest.mark.asyncio
async def test_score_history_is_persisted_and_ordered(session):
    repository = TraderRepository(session)
    now = datetime.now(UTC)
    trader = await repository.upsert_snapshot(
        NormalizedTrader(
            platform="mock",
            platform_trader_id="history-1",
            username="history",
            captured_at=now,
        )
    )
    metrics = {"win_rate": Metric(70, 80, True, "test")}
    for offset, value in enumerate((62, 67, 74, 82)):
        await repository.persist_score(
            trader.id,
            metrics,
            score(value, 70 + offset * 5),
            now + timedelta(days=offset),
        )
    history = await repository.get_score_history(trader.id)
    previous = await repository.get_previous_score(trader.id, history[-1].calculated_at)
    assert [float(item.whale_score) for item in history] == [62, 67, 74, 82]
    assert float(previous.whale_score) == 74
    assert float((await repository.get_latest_score(trader.id)).whale_score) == 82


def test_sustained_history_is_strongly_emerging():
    start = datetime(2026, 9, 1, tzinfo=UTC)
    scores = [(start + timedelta(days=i), value, 72 + i * 5) for i, value in enumerate((61, 64, 70, 78))]
    ranks = [(start + timedelta(days=i), rank) for i, rank in enumerate((800, 620, 350, 140))]
    result = analyze_history(scores, ranks, EmergingConfig())
    assert result.status == EmergingStatus.STRONGLY_EMERGING
    assert result.score_change == 17
    assert result.rank_change == 660


def test_spike_and_insufficient_history_are_rejected():
    start = datetime(2026, 9, 1, tzinfo=UTC)
    spike = [(start + timedelta(days=i), value, 75) for i, value in enumerate((90, 40, 91))]
    ranks = [(start + timedelta(days=i), rank) for i, rank in enumerate((100, 90, 80))]
    assert analyze_history(spike, ranks).status == EmergingStatus.NOT_EMERGING
    assert analyze_history(spike[:2], ranks[:2]).status == EmergingStatus.NOT_EMERGING
