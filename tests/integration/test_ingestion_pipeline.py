import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.mock import MockCollector
from app.database.models import Base, CollectionRun, Trade, Trader
from app.providers.mock import MockProvider
from app.services.ingestion_service import IngestionService


@pytest.mark.asyncio
async def test_mock_pipeline_persists_snapshots_trades_and_collection_run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        count = await IngestionService(
            MockCollector(MockProvider(seed=3, count=3)), detail_limit=3, concurrency=2
        ).collect_leaderboard(session)
        assert count == 3
        assert len((await session.scalars(select(Trader))).all()) == 3
        assert len((await session.scalars(select(Trade))).all()) == 15
        run = await session.scalar(select(CollectionRun))
        assert run.status == "success"
    await engine.dispose()
