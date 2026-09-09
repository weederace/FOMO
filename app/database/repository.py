from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers.metrics import Metric
from app.analyzers.whale_score import WhaleScore
from app.database.models import (
    BalanceSnapshot,
    CollectionRun,
    Trade,
    Trader,
    TraderScore,
    TraderSnapshot,
)
from app.schemas.trader import NormalizedBalance, NormalizedTrade, NormalizedTrader


class TraderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_platform_id(self, platform: str, platform_trader_id: str) -> Trader | None:
        return await self.session.scalar(
            select(Trader).where(Trader.platform == platform, Trader.platform_trader_id == platform_trader_id)
        )

    async def upsert_snapshot(self, item: NormalizedTrader) -> Trader:
        trader = await self.session.scalar(
            select(Trader).where(
                Trader.platform == item.platform,
                Trader.platform_trader_id == item.platform_trader_id,
            )
        )
        if trader is None:
            trader = Trader(platform=item.platform, platform_trader_id=item.platform_trader_id)
            self.session.add(trader)
        trader.username, trader.display_name, trader.profile_url = (
            item.username,
            item.display_name,
            item.profile_url,
        )
        trader.wallet_address = item.wallet_address
        trader.wallets = item.wallets or ({"primary": item.wallet_address} if item.wallet_address else {})
        if trader.last_seen_at is None or item.captured_at.replace(tzinfo=None) >= trader.last_seen_at.replace(tzinfo=None):
            trader.last_seen_at = item.captured_at
        await self.session.flush()
        exists = await self.session.scalar(
            select(TraderSnapshot).where(
                TraderSnapshot.trader_id == trader.id,
                TraderSnapshot.captured_at == item.captured_at,
            )
        )
        if exists is None:
            self.session.add(
                TraderSnapshot(
                    trader_id=trader.id,
                    captured_at=item.captured_at,
                    rank=item.rank,
                    pnl=item.pnl,
                    roi=item.roi,
                    volume=item.volume,
                    follower_count=item.follower_count,
                    total_trades=item.total_trades,
                    wins=item.wins,
                    losses=item.losses,
                    source=item.platform,
                )
            )
        return trader

    async def persist_trade(self, trader_id: int, item: NormalizedTrade) -> Trade | None:
        external = item.platform_trade_id or ""
        key = f"{item.platform}:{trader_id}:{external or item.token_address or item.token_symbol}:{item.executed_at or item.captured_at}"
        exists = await self.session.scalar(
            select(Trade).where(
                (Trade.trade_key == key)
                | ((Trade.trader_id == trader_id) & (Trade.platform_trade_id == item.platform_trade_id))
            )
        )
        if exists:
            return None  # already known; callers use this to count only new trades
        trade = Trade(
            trader_id=trader_id,
            platform_trade_id=item.platform_trade_id,
            token_symbol=item.token_symbol,
            token_address=item.token_address,
            chain=item.chain,
            side=item.side,
            size_usd=item.size_usd,
            price=item.price,
            market_cap=item.market_cap,
            quantity=item.quantity,
            status=item.status,
            realized_pnl_usd=item.realized_pnl_usd,
            unrealized_pnl_usd=item.unrealized_pnl_usd,
            holding_duration_seconds=item.holding_duration_seconds,
            transaction_hash=item.transaction_hash,
            executed_at=item.executed_at,
            captured_at=item.captured_at,
            trade_key=key,
        )
        self.session.add(trade)
        await self.session.flush()
        return trade

    async def persist_balance(self, trader_id: int, item: NormalizedBalance) -> BalanceSnapshot | None:
        key = f"{item.platform}:{trader_id}:{item.chain}:{item.token_address or item.token_symbol}:{item.captured_at}"
        exists = await self.session.scalar(select(BalanceSnapshot).where(BalanceSnapshot.balance_key == key))
        if exists:
            return None  # already captured at this timestamp
        balance = BalanceSnapshot(
            trader_id=trader_id, token_symbol=item.token_symbol, token_address=item.token_address,
            chain=item.chain, amount=item.amount, value_usd=item.value_usd,
            captured_at=item.captured_at, balance_key=key,
        )
        self.session.add(balance)
        await self.session.flush()
        return balance

    async def start_collection_run(self, provider: str, started_at: datetime) -> CollectionRun:
        run = CollectionRun(started_at=started_at, provider=provider, status="running")
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish_collection_run(
        self, run: CollectionRun, finished_at: datetime, status: str,
        traders_seen: int, trades_seen: int, errors: list[str], duration_ms: int,
    ) -> CollectionRun:
        run.finished_at, run.status = finished_at, status
        run.traders_seen, run.trades_seen = traders_seen, trades_seen
        run.errors, run.duration_ms = errors, duration_ms
        await self.session.flush()
        return run

    async def persist_score(
        self,
        trader_id: int,
        metrics: dict[str, Metric],
        result: WhaleScore,
        calculated_at: datetime | None = None,
    ) -> TraderScore:
        values = {name: metric.value for name, metric in metrics.items()}
        score = TraderScore(
            trader_id=trader_id,
            calculated_at=calculated_at or datetime.now(UTC),
            consistency_score=values.get("consistency"),
            win_rate_score=values.get("win_rate"),
            risk_adjusted_score=values.get("risk_adjusted"),
            early_entry_score=values.get("early_entry"),
            trade_quality_score=values.get("trade_quality"),
            activity_score=values.get("activity"),
            whale_score=result.score,
            score_confidence=result.confidence,
            classification=result.classification,
        )
        self.session.add(score)
        await self.session.flush()
        return score

    async def get_latest_score(self, trader_id: int) -> TraderScore | None:
        return await self.session.scalar(
            select(TraderScore)
            .where(TraderScore.trader_id == trader_id)
            .order_by(TraderScore.calculated_at.desc(), TraderScore.id.desc())
            .limit(1)
        )

    async def get_score_history(self, trader_id: int, limit: int | None = None) -> list[TraderScore]:
        query = (
            select(TraderScore)
            .where(TraderScore.trader_id == trader_id)
            .order_by(TraderScore.calculated_at.asc(), TraderScore.id.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        return list((await self.session.scalars(query)).all())

    async def get_score_history_between(
        self, trader_id: int, start: datetime, end: datetime
    ) -> list[TraderScore]:
        return list(
            (
                await self.session.scalars(
                    select(TraderScore)
                    .where(
                        TraderScore.trader_id == trader_id,
                        TraderScore.calculated_at >= start,
                        TraderScore.calculated_at <= end,
                    )
                    .order_by(TraderScore.calculated_at.asc(), TraderScore.id.asc())
                )
            ).all()
        )

    async def get_previous_score(self, trader_id: int, before: datetime | None = None) -> TraderScore | None:
        query = select(TraderScore).where(TraderScore.trader_id == trader_id)
        if before is not None:
            query = query.where(TraderScore.calculated_at < before)
        return await self.session.scalar(
            query.order_by(TraderScore.calculated_at.desc(), TraderScore.id.desc()).limit(1)
        )

    async def get_snapshot_history(self, trader_id: int, start: datetime) -> list[TraderSnapshot]:
        return list(
            (
                await self.session.scalars(
                    select(TraderSnapshot)
                    .where(
                        TraderSnapshot.trader_id == trader_id,
                        TraderSnapshot.captured_at >= start,
                    )
                    .order_by(TraderSnapshot.captured_at.asc(), TraderSnapshot.id.asc())
                )
            ).all()
        )

    async def list_traders(self, limit: int = 50, offset: int = 0) -> list[Trader]:
        return list(
            (
                await self.session.scalars(
                    select(Trader).order_by(Trader.id).offset(offset).limit(limit)
                )
            ).all()
        )

    async def top_scores(self, limit: int = 20) -> list[TraderScore]:
        return list(
            (
                await self.session.scalars(
                    select(TraderScore).order_by(desc(TraderScore.whale_score)).limit(limit)
                )
            ).all()
        )

    async def get_trades(self, trader_id: int, limit: int = 100, offset: int = 0) -> list[Trade]:
        return list((await self.session.scalars(
            select(Trade).where(Trade.trader_id == trader_id)
            .order_by(Trade.executed_at.desc().nullslast(), Trade.id.desc())
            .offset(offset).limit(limit)
        )).all())

    async def get_snapshots(self, trader_id: int, limit: int = 100, offset: int = 0) -> list[TraderSnapshot]:
        return list((await self.session.scalars(
            select(TraderSnapshot).where(TraderSnapshot.trader_id == trader_id)
            .order_by(TraderSnapshot.captured_at.desc(), TraderSnapshot.id.desc())
            .offset(offset).limit(limit)
        )).all())

    async def latest_collection_run(self) -> CollectionRun | None:
        return await self.session.scalar(
            select(CollectionRun).order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc()).limit(1)
        )
