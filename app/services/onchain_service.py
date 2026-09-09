from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.database.models import Trader
from app.database.repository import TraderRepository
from app.providers.onchain import OnchainScanner


async def scan_wallets(session: AsyncSession, settings: Settings) -> int:
    """Scan a bounded number of known wallets and persist transfer events."""
    if not settings.chain_scan_enabled:
        return 0
    traders = list((await session.scalars(
        select(Trader).where(Trader.is_active.is_(True), Trader.wallet_address.is_not(None))
        .limit(settings.chain_scan_wallet_limit)
    )).all())
    scanner = OnchainScanner(settings)
    saved = 0
    try:
        repository = TraderRepository(session)
        for trader in traders:
            try:
                wallets = trader.wallets or {"primary": trader.wallet_address}
                for wallet in {value for value in wallets.values() if value}:
                    for item in await scanner.scan_wallet(str(trader.id), wallet):
                        if await repository.persist_trade(trader.id, item):
                            saved += 1
            except Exception:
                # A failing chain or malformed contract must not stop leaderboard ingestion.
                continue
    finally:
        await scanner.aclose()
    return saved
