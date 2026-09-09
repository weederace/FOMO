"""Continuous collection worker. It performs no trading or transaction signing."""

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.leaderboard import LeaderboardCollector  # noqa: E402
from app.config.settings import get_settings  # noqa: E402
from app.database.models import Base  # noqa: E402
from app.database.session import SessionLocal, engine  # noqa: E402
from app.monitoring.deduplication import RedisDeduplicator  # noqa: E402
from app.providers.factory import create_provider  # noqa: E402
from app.providers.gmgn import GmgnClient  # noqa: E402
from app.services.alert_service import AlertService  # noqa: E402
from app.services.emerging_service import find_emerging_traders  # noqa: E402
from app.services.gmgn_market import refresh_market  # noqa: E402
from app.services.gmgn_service import enrich_wallets  # noqa: E402
from app.services.ingestion_service import IngestionService  # noqa: E402
from app.services.onchain_service import scan_wallets  # noqa: E402
from app.telegram.bot import TelegramNotifier  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402

LOGGER = logging.getLogger("worker")


async def run() -> None:
    settings = get_settings()
    provider = create_provider(settings)
    alert_service = AlertService(
        deduplicator=RedisDeduplicator(settings.redis_url, settings.alert_cooldown_seconds),
        notifier=TelegramNotifier(settings),
    )
    gmgn_client = GmgnClient(
        command=settings.gmgn_cli_command,
        request_timeout_seconds=settings.gmgn_request_timeout_seconds,
    ) if settings.gmgn_enabled else None
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    last_onchain_scan = 0.0
    last_gmgn_enrich = 0.0
    last_gmgn_market = 0.0
    try:
        while True:
            # Every stage runs inside its own try/except: a hung or failing
            # GMGN call (or the provider) must never kill the loop — a dead
            # loop silently froze alerts and all market feeds for hours.
            async with SessionLocal() as session:
                try:
                    await IngestionService(
                        LeaderboardCollector(provider), provider,
                        detail_limit=settings.detail_collection_limit,
                        concurrency=settings.max_concurrent_provider_requests,
                        alert_service=alert_service,
                        alert_threshold=settings.watchlist_score_threshold,
                        confidence_threshold=settings.watchlist_confidence_threshold,
                    ).collect_leaderboard(session)
                    await session.commit()
                except Exception:
                    LOGGER.exception("leaderboard collection failed; retrying next cycle")
                # GMGN stages run BEFORE the slow on-chain scan: the scan walks
                # 50 wallets across 3 chains and can take 15+ minutes with
                # provider fallbacks; if it ran first it would starve the
                # market feeds that generate the Alerts tab.
                if gmgn_client is not None:
                    if time.monotonic() - last_gmgn_market >= settings.gmgn_market_interval_seconds:
                        try:
                            market = await refresh_market(session, settings, gmgn_client)
                            LOGGER.info(
                                "GMGN market radar: %s feeds, %s new alerts",
                                market["feeds"], market["alerts"],
                            )
                        except Exception:
                            LOGGER.exception("GMGN market refresh failed; skipping this window")
                        last_gmgn_market = time.monotonic()
                    if time.monotonic() - last_gmgn_enrich >= settings.gmgn_enrich_interval_seconds:
                        try:
                            summary = await enrich_wallets(session, settings, gmgn_client)
                            LOGGER.info(
                                "GMGN wallet enrichment: %s wallets, %s trades, %s balances, %s errors",
                                summary["wallets"], summary["saved_trades"],
                                summary["saved_balances"], summary["errors"],
                            )
                        except Exception:
                            LOGGER.exception("GMGN enrichment failed; skipping this window")
                        last_gmgn_enrich = time.monotonic()
                    # Commit immediately: alerts and trades must reach the API
                    # without waiting for the multi-minute on-chain scan that
                    # follows in the same loop iteration.
                    await session.commit()
                if settings.chain_scan_enabled and time.monotonic() - last_onchain_scan >= settings.chain_scan_interval_seconds:
                    try:
                        await scan_wallets(session, settings)
                    except Exception:
                        LOGGER.exception("on-chain scan failed; skipping this window")
                    last_onchain_scan = time.monotonic()
                    await session.commit()
                try:
                    emerging = await find_emerging_traders(session, settings, 50)
                    for item in emerging:
                        await alert_service.create_alert(
                            session, item["trader"]["id"], "EMERGING_WHALE", item,
                            f"{item['history_points']}:{item['score_change']}:{item['emerging_status']}",
                        )
                except Exception:
                    LOGGER.exception("emerging-trader scan failed; skipping this window")
                if provider.capabilities().get("alerts"):
                    try:
                        for event in await provider.get_alerts(50):
                            await alert_service.create_provider_alert(session, event)
                    except Exception:
                        LOGGER.exception("provider alert pull failed; skipping this window")
                await session.commit()
            await asyncio.sleep(settings.leaderboard_interval_seconds)
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()
        if gmgn_client is not None:
            await gmgn_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
