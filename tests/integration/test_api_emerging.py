from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analyzers.metrics import Metric
from app.analyzers.whale_score import WhaleScore
from app.api.main import app
from app.database.models import Base
from app.database.repository import TraderRepository
from app.database.session import get_session
from app.schemas.trader import NormalizedTrader


@pytest.mark.asyncio
async def test_emerging_api_returns_historical_analysis():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        repository = TraderRepository(session)
        trader = await repository.upsert_snapshot(
            NormalizedTrader(platform="mock", platform_trader_id="api-1", username="api", captured_at=now)
        )
        for i, (value, rank) in enumerate(((61, 800), (64, 620), (70, 350), (78, 140))):
            timestamp = now - timedelta(days=3 - i)
            await repository.upsert_snapshot(
                NormalizedTrader(
                    platform="mock", platform_trader_id="api-1", username="api",
                    rank=rank, captured_at=timestamp,
                )
            )
            await repository.persist_score(
                trader.id, {"win_rate": Metric(70, 80, True, "test")},
                WhaleScore(value, 72 + i * 5, "SMART WHALE"), timestamp,
            )
        await session.commit()

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/traders/emerging")
        assert response.status_code == 200
        payload = response.json()
        assert payload[0]["emerging_status"] == "STRONGLY_EMERGING"
        assert payload[0]["history_points"] == 4
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
