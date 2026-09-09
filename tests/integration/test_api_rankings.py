"""Integration tests for /traders/rankings identity fields."""

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
async def test_rankings_growth_returns_trader_identity():
    """Growth rankings must carry handle/display_name/wallet/platform so the
    UI shows real usernames instead of "trader #N" placeholders."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        repository = TraderRepository(session)
        trader = await repository.upsert_snapshot(
            NormalizedTrader(
                platform="fomoapi", platform_trader_id="whale-one", username="whaleOne",
                display_name="Whale One", wallet_address="9WzDxK" * 5 + "AA",
                captured_at=now - timedelta(days=2),
            )
        )
        await repository.upsert_snapshot(
            NormalizedTrader(
                platform="fomoapi", platform_trader_id="whale-one", username="whaleOne",
                display_name="Whale One", wallet_address="9WzDxK" * 5 + "AA",
                captured_at=now,
            )
        )
        for i, value in enumerate((50, 80)):
            timestamp = now - timedelta(days=2 - i)
            await repository.persist_score(
                trader.id, {"win_rate": Metric(70, 80, True, "test")},
                WhaleScore(value, 80, "SMART WHALE"), timestamp,
            )
        await session.commit()

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/traders/rankings?kind=growth&limit=10")
        assert response.status_code == 200
        rows = response.json()
        assert rows, "expected at least one ranked trader"
        row = rows[0]
        assert row["handle"] == "whaleOne"
        assert row["display_name"] == "Whale One"
        assert row["wallet"] == "9WzDxK" * 5 + "AA"
        assert row["platform"] == "fomoapi"
        assert row["score_growth"] == pytest.approx(30.0)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_rankings_score_kind_also_returns_identity():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        repository = TraderRepository(session)
        trader = await repository.upsert_snapshot(
            NormalizedTrader(
                platform="fomo-crawl", platform_trader_id="crawl-user",
                username="crawl-user", captured_at=now,
            )
        )
        await repository.persist_score(
            trader.id, {"win_rate": Metric(70, 80, True, "test")},
            WhaleScore(66, 75, "SMART WHALE"), now,
        )
        await session.commit()

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/traders/rankings?kind=score&limit=10")
        assert response.status_code == 200
        row = response.json()[0]
        assert row["handle"] == "crawl-user"
        assert row["platform"] == "fomo-crawl"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
