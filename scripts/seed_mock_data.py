import asyncio

from app.collectors.mock import MockCollector
from app.database.models import Base
from app.database.session import SessionLocal, engine
from app.providers.mock import MockProvider
from app.services.ingestion_service import IngestionService


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await IngestionService(MockCollector(MockProvider())).collect_leaderboard(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
