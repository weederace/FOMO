"""Run one configured leaderboard collection cycle."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.leaderboard import LeaderboardCollector
from app.config.settings import get_settings
from app.database.models import Base
from app.database.session import SessionLocal, engine
from app.providers.factory import create_provider
from app.services.ingestion_service import IngestionService
from app.services.onchain_service import scan_wallets
from app.utils.logging import configure_logging


async def run() -> None:
    settings = get_settings()
    provider = create_provider(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with SessionLocal() as session:
            count = await IngestionService(
                LeaderboardCollector(provider), provider,
                detail_limit=settings.detail_collection_limit,
                concurrency=settings.max_concurrent_provider_requests,
            ).collect_leaderboard(session)
            onchain_count = await scan_wallets(session, settings)
            await session.commit()
            print(f"Collected {count} traders and {onchain_count} on-chain transfers from {provider.name}")
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
        await engine.dispose()


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(run())
