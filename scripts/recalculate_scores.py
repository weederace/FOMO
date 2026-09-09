"""Recalculate score snapshots from stored trades without external calls."""
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analyzers.metrics import calculate_trade_summary
from app.analyzers.whale_score import calculate
from app.database.models import Base, Trader
from app.database.repository import TraderRepository
from app.database.session import SessionLocal, engine


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        repository = TraderRepository(session)
        traders = list((await session.scalars(select(Trader))).all())
        for trader in traders:
            summary = calculate_trade_summary(await repository.get_trades(trader.id, 500))
            if summary.metrics:
                await repository.persist_score(trader.id, summary.metrics, calculate(summary.metrics, summary.total_trades), datetime.now(UTC))
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
