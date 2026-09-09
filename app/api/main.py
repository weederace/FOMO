import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analyzers.live_analytics import flow_summary, snapshot_delta, trader_archetype, whale_score
from app.config.settings import get_settings
from app.database.models import (
    Alert,
    Base,
    CollectionRun,
    Trade,
    Trader,
    TraderScore,
    TraderSnapshot,
)
from app.database.repository import TraderRepository
from app.database.session import engine, get_session
from app.providers.factory import create_provider
from app.services.emerging_service import find_emerging_traders
from app.services.gmgn_market import market_cache
from app.services.market_data import token_market_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    create_provider(settings)
    if settings.mock_mode or settings.database_url.startswith("sqlite"):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="FOMO Whale Intelligence", version="0.2.0", lifespan=lifespan)


def trader_dict(item: Trader) -> dict:
    return {
        "id": item.id, "platform": item.platform, "platform_trader_id": item.platform_trader_id,
        "username": item.username, "display_name": item.display_name, "profile_url": item.profile_url,
        "wallet_address": item.wallet_address, "last_seen_at": item.last_seen_at, "is_active": item.is_active,
        "wallets": item.wallets or ({"primary": item.wallet_address} if item.wallet_address else {}),
    }


def score_dict(item: TraderScore) -> dict:
    return {
        "id": item.id, "trader_id": item.trader_id, "calculated_at": item.calculated_at,
        "whale_score": float(item.whale_score) if item.whale_score is not None else None,
        "score_confidence": float(item.score_confidence) if item.score_confidence is not None else None,
        "classification": item.classification,
        "components": {name: float(value) if value is not None else None for name, value in {
            "consistency": item.consistency_score, "win_rate": item.win_rate_score,
            "risk_adjusted": item.risk_adjusted_score, "early_entry": item.early_entry_score,
            "trade_quality": item.trade_quality_score, "activity": item.activity_score,
        }.items()},
    }


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "mode": settings.data_provider}


@app.get("/provider/capabilities")
async def provider_capabilities() -> dict:
    provider = create_provider(get_settings())
    try:
        result = {"provider": provider.name, "capabilities": provider.capabilities()}
        metrics = getattr(provider, "request_metrics", None)
        if metrics:
            result["request_metrics"] = metrics()
        return result
    finally:
        close = getattr(provider, "aclose", None)
        if close:
            await close()


@app.get("/provider/status")
async def provider_status(session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    if settings.data_provider == "mock":
        return {"provider": "mock", "status": "MOCK", "capabilities": create_provider(settings).capabilities()}
    run = await TraderRepository(session).latest_collection_run()
    provider = create_provider(settings)
    if run is None:
        state = "OFFLINE"
    elif run.status == "partial":
        state = "DEGRADED"
    else:
        state = "LIVE"
    return {
        "provider": provider.name, "status": state, "last_collection": run.finished_at if run else None,
        "last_error": run.errors if run and run.errors else None,
        "traders_seen": run.traders_seen if run else 0,
        "capabilities": provider.capabilities(),
    }


@app.get("/traders")
async def traders(
    session: AsyncSession = Depends(get_session), limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0), platform: str | None = None,
):
    query = select(Trader).order_by(Trader.id).offset(offset).limit(limit)
    if platform:
        query = query.where(Trader.platform == platform)
    return [trader_dict(item) for item in (await session.scalars(query)).all()]


@app.get("/traders/top")
async def top(limit: int = Query(20, ge=1, le=100), session: AsyncSession = Depends(get_session)):
    scores = await TraderRepository(session).top_scores(limit)
    return [score_dict(item) for item in scores]


@app.get("/traders/emerging")
async def emerging(session: AsyncSession = Depends(get_session), limit: int = Query(50, ge=1, le=200)):
    return await find_emerging_traders(session, get_settings(), limit)


@app.get("/traders/rankings")
async def rankings(
    kind: str = Query("score", pattern="^(score|growth|pnl|activity|win_rate)$"),
    limit: int = Query(20, ge=1, le=100), session: AsyncSession = Depends(get_session),
    ):
    scores = list((await session.scalars(select(TraderScore))).all())
    latest: dict[int, TraderScore] = {}
    for item in scores:
        if item.trader_id not in latest or (item.calculated_at, item.id) > (latest[item.trader_id].calculated_at, latest[item.trader_id].id):
            latest[item.trader_id] = item
    # Identity info for every scored trader so the UI never has to fall back
    # to "trader #N" labels — the dashboard rows only cover the top slice.
    traders: dict[int, Trader] = {
        item.id: item
        for item in (await session.scalars(select(Trader).where(Trader.id.in_(latest.keys())))).all()
    }
    if get_settings().data_provider != "mock":
        traders = {key: item for key, item in traders.items() if item.platform != "mock"}
        latest = {key: item for key, item in latest.items() if key in traders}

    # The persisted whale_score is None whenever the scorer had no sufficient
    # metric (the FOMO leaderboard ships no win/loss split). Fall back to the
    # same live snapshot score the dashboard uses so Movers never shows "—".
    latest_snapshot: dict[int, TraderSnapshot] = {}
    oldest_snapshot: dict[int, TraderSnapshot] = {}
    for item in (await session.scalars(select(TraderSnapshot).order_by(TraderSnapshot.captured_at))).all():
        latest_snapshot[item.trader_id] = item
        oldest_snapshot.setdefault(item.trader_id, item)

    def live_score(trader_id: int) -> float | None:
        snapshot = latest_snapshot.get(trader_id)
        if snapshot is None:
            return None
        return whale_score({
            "pnl": snapshot.pnl, "volume": snapshot.volume, "trades": snapshot.total_trades,
            "followers": snapshot.follower_count,
        })

    def score_value(trader_id: int) -> float | None:
        persisted = latest[trader_id].whale_score
        return float(persisted) if persisted is not None else live_score(trader_id)

    def oldest_score_value(trader_id: int) -> float | None:
        # Growth needs an oldest point. The persisted history is usually NULL
        # (same scorer limitation as above), so derive the oldest point from the
        # oldest snapshot — every tracked trader has one — instead of "—".
        oldest = oldest_snapshot.get(trader_id)
        if oldest is None:
            return None
        return whale_score({
            "pnl": oldest.pnl, "volume": oldest.volume, "trades": oldest.total_trades,
            "followers": oldest.follower_count,
        })

    def trader_info(trader_id: int) -> dict:
        trader = traders.get(trader_id)
        if trader is None:
            return {}
        return {
            "handle": trader.username or trader.platform_trader_id,
            "display_name": trader.display_name,
            "wallet": trader.wallet_address,
            "platform": trader.platform,
        }

    if kind == "pnl":
        snapshots = list((await session.scalars(select(TraderSnapshot))).all())
        current = {}
        for snapshot in snapshots:
            if snapshot.trader_id not in current or snapshot.captured_at > current[snapshot.trader_id].captured_at:
                current[snapshot.trader_id] = snapshot
        return [
            {**trader_info(item.trader_id), "trader_id": item.trader_id, "pnl": float(item.pnl) if item.pnl is not None else None, "rank": item.rank, "captured_at": item.captured_at}
            for item in sorted(current.values(), key=lambda item: float(item.pnl) if item.pnl is not None else -float("inf"), reverse=True)[:limit]
        ]
    if kind == "growth":
        values = []
        repo = TraderRepository(session)
        for trader_id, item in latest.items():
            history = await repo.get_score_history(trader_id)
            current_value = score_value(trader_id)
            oldest = next(
                (float(row.whale_score) for row in history if row.whale_score is not None),
                None,
            )
            if oldest is None:
                oldest = oldest_score_value(trader_id)
            growth = (
                float(current_value - oldest)
                if current_value is not None and oldest is not None and len(history) >= 2
                else None
            )
            values.append((growth, item))
        values.sort(key=lambda pair: pair[0] if pair[0] is not None else -float("inf"), reverse=True)
        return [
            {**trader_info(item.trader_id), **score_dict(item),
             "whale_score": score_value(item.trader_id), "score_growth": growth}
            for growth, item in values[:limit]
        ]
    field = {"score": "whale_score", "pnl": "whale_score", "activity": "activity_score", "win_rate": "win_rate_score"}[kind]
    if field == "whale_score":
        ranked = sorted(latest.keys(), key=lambda key: score_value(key) or -float("inf"), reverse=True)
    else:
        ranked = sorted(latest.keys(), key=lambda key: getattr(latest[key], field) or -float("inf"), reverse=True)
    return [
        {**trader_info(key), **score_dict(latest[key]),
         "whale_score": score_value(key)}
        for key in ranked[:limit]
    ]


@app.get("/traders/{trader_id}/history")
async def trader_history(trader_id: int, session: AsyncSession = Depends(get_session), limit: int = Query(100, ge=1, le=500)):
    if not await session.get(Trader, trader_id):
        raise HTTPException(404, "Trader not found")
    return [item.__dict__ for item in await TraderRepository(session).get_snapshots(trader_id, limit)]


@app.get("/traders/{trader_id}/trades")
async def trader_trades(trader_id: int, session: AsyncSession = Depends(get_session), limit: int = Query(100, ge=1, le=500)):
    if not await session.get(Trader, trader_id):
        raise HTTPException(404, "Trader not found")
    return [item.__dict__ for item in await TraderRepository(session).get_trades(trader_id, limit)]


@app.get("/traders/{trader_id}/score")
async def trader_score(trader_id: int, session: AsyncSession = Depends(get_session)):
    item = await TraderRepository(session).get_latest_score(trader_id)
    if item is None:
        raise HTTPException(404, "Score not found")
    return score_dict(item)


@app.get("/traders/{trader_id}/score-history")
async def trader_score_history(trader_id: int, session: AsyncSession = Depends(get_session), limit: int = Query(100, ge=1, le=500)):
    return [score_dict(item) for item in await TraderRepository(session).get_score_history(trader_id, limit)]


@app.get("/traders/{trader_id}")
async def trader(trader_id: int, session: AsyncSession = Depends(get_session)):
    item = await session.get(Trader, trader_id)
    if item is None:
        raise HTTPException(404, "Trader not found")
    return trader_dict(item)


@app.get("/leaderboard")
async def leaderboard(session: AsyncSession = Depends(get_session), limit: int = Query(50, ge=1, le=200)):
    latest = select(
        TraderSnapshot.trader_id, func.max(TraderSnapshot.captured_at).label("captured_at")
    ).group_by(TraderSnapshot.trader_id).subquery()
    rows = (await session.execute(
        select(Trader, TraderSnapshot).join(TraderSnapshot, TraderSnapshot.trader_id == Trader.id)
        .join(latest, (latest.c.trader_id == TraderSnapshot.trader_id) & (latest.c.captured_at == TraderSnapshot.captured_at))
        .order_by(TraderSnapshot.rank.asc().nullslast()).limit(limit)
    )).all()
    return [{**trader_dict(trader), "snapshot": snapshot.__dict__} for trader, snapshot in rows]


@app.get("/alerts")
async def alerts(session: AsyncSession = Depends(get_session), limit: int = Query(50, ge=1, le=200)):
    return list((await session.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(limit))).all())


@app.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)):
    return {
        "traders": len((await session.scalars(select(Trader))).all()),
        "scores": len((await session.scalars(select(TraderScore))).all()),
    }


# ---------- GMGN market radar + wallet intel ----------

def _gmgn_settings():
    settings = get_settings()
    if not settings.gmgn_enabled:
        raise HTTPException(404, "GMGN integration is disabled; set GMGN_ENABLED=true")
    return settings


def _gmgn_client(settings):
    from app.providers.gmgn import GmgnClient

    return GmgnClient(
        command=settings.gmgn_cli_command,
        request_timeout_seconds=settings.gmgn_request_timeout_seconds,
        auto_install=False,  # never install from a web request
    )


@app.get("/gmgn/status")
async def gmgn_status():
    settings = get_settings()
    from app.providers.gmgn import describe_setup

    setup = describe_setup(settings.gmgn_cli_command)
    return {
        "enabled": settings.gmgn_enabled,
        "cached_feeds": sorted(market_cache().get(key) is not None for key in ()) or [
            key for key in ("trending", "trenches", "hot_searches") if market_cache().get(key) is not None
        ],
        **setup,
    }


@app.get("/gmgn/trending")
async def gmgn_trending(chain: str | None = None, interval: str = Query("5m", pattern="^(1m|5m|1h|6h|24h)$")):
    settings = _gmgn_settings()
    key = f"trending:{chain or settings.gmgn_default_chain}:{interval}"
    cached = market_cache().get(key)
    if cached is not None:
        return {"source": "cache", "rows": cached}
    try:
        client = _gmgn_client(settings)
        rows = await client.market_trending(
            chain or settings.gmgn_default_chain, interval, limit=settings.gmgn_market_row_limit
        )
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    from app.services.gmgn_market import _token_row

    normalized = [_token_row(chain or settings.gmgn_default_chain, row) for row in rows]
    market_cache().set(key, normalized)
    return {"source": "cli", "rows": normalized}


@app.get("/gmgn/trenches")
async def gmgn_trenches(chain: str | None = None):
    settings = _gmgn_settings()
    key = f"trenches:{chain or settings.gmgn_default_chain}"
    cached = market_cache().get(key)
    if cached is not None:
        return {"source": "cache", **cached}
    try:
        client = _gmgn_client(settings)
        buckets = await client.market_trenches(
            chain or settings.gmgn_default_chain, limit=settings.gmgn_market_row_limit
        )
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    from app.services.gmgn_market import _token_row

    normalized = {
        kind: [_token_row(chain or settings.gmgn_default_chain, row) for row in rows]
        for kind, rows in buckets.items()
    }
    market_cache().set(key, normalized)
    return {"source": "cli", **normalized}


@app.get("/gmgn/hot-searches")
async def gmgn_hot_searches(interval: str = Query("5m", pattern="^(1m|5m|1h|6h|24h)$")):
    settings = _gmgn_settings()
    key = f"hot_searches:{interval}"
    cached = market_cache().get(key)
    if cached is not None:
        return {"source": "cache", "rows": cached}
    try:
        client = _gmgn_client(settings)
        rows = await client.market_hot_searches(interval, limit=settings.gmgn_market_row_limit)
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    from app.services.gmgn_market import _token_row

    normalized = [_token_row(settings.gmgn_default_chain, row) for row in rows]
    market_cache().set(key, normalized)
    return {"source": "cli", "rows": normalized}


@app.get("/gmgn/token/{chain}/{address}")
async def gmgn_token(chain: str, address: str):
    settings = _gmgn_settings()
    try:
        client = _gmgn_client(settings)
        info, security, pool = await asyncio.gather(
            client.token_info(chain, address),
            client.token_security(chain, address),
            client.token_pool(chain, address),
        )
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    return {"chain": chain, "address": address, "info": info, "security": security, "pool": pool}


@app.get("/gmgn/wallet/{chain}/{wallet}")
async def gmgn_wallet(chain: str, wallet: str):
    settings = _gmgn_settings()
    try:
        client = _gmgn_client(settings)
        stats = await client.portfolio_stats(chain, [wallet])
        holdings = await client.portfolio_holdings(chain, wallet, limit=settings.gmgn_holdings_limit)
        created = await client.portfolio_created_tokens(chain, wallet)
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    return {
        "chain": chain,
        "wallet": wallet,
        "stats": stats[0].model_dump(mode="json") if stats else None,
        "holdings": holdings,
        "created_tokens": created,
    }


@app.get("/gmgn/wallet/{chain}/{wallet}/score")
async def gmgn_wallet_score(chain: str, wallet: str):
    settings = _gmgn_settings()
    try:
        client = _gmgn_client(settings)
        stats = await client.portfolio_stats(chain, [wallet])
        activity = await client.portfolio_activity(chain, wallet, limit=settings.gmgn_activity_limit)
        await client.aclose()
    except Exception as exc:
        raise HTTPException(503, f"gmgn-cli unavailable: {type(exc).__name__}") from exc
    from app.services.gmgn_service import wallet_score

    base = wallet_score(stats[0]) if stats else {"score": None, "verdict": "UNKNOWN — no stats", "style": "unknown"}
    buys = sum(1 for row in activity if str(row.get("event_type") or row.get("type") or "").lower() == "buy")
    sells = sum(1 for row in activity if str(row.get("event_type") or row.get("type") or "").lower() == "sell")
    base["recent_activity"] = {"buys": buys, "sells": sells, "events": len(activity)}
    return {"chain": chain, "wallet": wallet, **base}


@app.get("/dashboard")
async def dashboard(
    session: AsyncSession = Depends(get_session), limit: int = Query(100, ge=1, le=200),
):
    """Return one UI-ready snapshot so the desktop dashboard needs one request."""
    traders = list((await session.scalars(select(Trader).where(Trader.is_active.is_(True)))).all())
    if get_settings().data_provider != "mock":
        # Mock rows are only seed/demo data: keep them out of the live feed
        # unless the app is actually running in mock mode.
        traders = [item for item in traders if item.platform != "mock"]
    snapshots = list((await session.scalars(select(TraderSnapshot).order_by(TraderSnapshot.captured_at))).all())
    trades = token_trades = list((await session.scalars(
        select(Trade).where(Trade.captured_at >= datetime.now(UTC) - timedelta(hours=24))
    )).all())
    alerts = list((await session.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(50))).all())
    last_run = (await session.scalars(select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(1))).first()

    latest_snapshot: dict[int, TraderSnapshot] = {}
    previous_snapshot: dict[int, TraderSnapshot] = {}
    for item in snapshots:
        if item.trader_id in latest_snapshot:
            previous_snapshot[item.trader_id] = latest_snapshot[item.trader_id]
        latest_snapshot[item.trader_id] = item
    latest_scores: dict[int, TraderScore] = {}
    for item in (await session.scalars(select(TraderScore).order_by(TraderScore.calculated_at))).all():
        latest_scores[item.trader_id] = item
    rows = []
    for trader in traders:
        current = latest_snapshot.get(trader.id)
        if current is None:
            continue
        raw = {
            "pnl": current.pnl, "volume": current.volume, "trades": current.total_trades,
            "followers": current.follower_count,
        }
        row = {
            "id": trader.id, "handle": trader.username or trader.platform_trader_id,
            "display_name": trader.display_name, "wallet": trader.wallet_address,
            "rank": current.rank, "pnl": float(current.pnl) if current.pnl is not None else None,
            "volume": float(current.volume) if current.volume is not None else None,
            "trades": current.total_trades, "followers": current.follower_count,
            "whale_score": whale_score(raw),
            "archetype": trader_archetype(raw),
            "delta": snapshot_delta(raw, {
                "pnl": previous_snapshot[trader.id].pnl,
                "trades": previous_snapshot[trader.id].total_trades,
                "volume": previous_snapshot[trader.id].volume,
            } if trader.id in previous_snapshot else None),
        }
        rows.append(row)
    rows.sort(key=lambda item: item["whale_score"] or 0, reverse=True)

    leaderboard_volume = sum(row["volume"] or 0 for row in rows)
    top_whale = max(rows, key=lambda item: item["whale_score"] or 0, default=None)
    classified_trades = [
        item for item in token_trades
        if str(item.side or "").lower() in {"buy", "bought", "sell", "sold"}
        and (item.token_symbol or "").upper() != "MOCK"
    ]
    trade_volume = sum(float(item.size_usd) for item in classified_trades if item.size_usd is not None)
    token_counts = Counter(str(item.token_symbol or "UNKNOWN") for item in trades)
    flow_trades = [{
        "token_symbol": item.token_symbol, "token_address": item.token_address,
        "chain": item.chain, "side": item.side, "status": item.status,
        "size_usd": item.size_usd, "quantity": item.quantity,
        "realized_pnl_usd": item.realized_pnl_usd,
        "executed_at": item.executed_at, "captured_at": item.captured_at,
    } for item in trades]
    for alert in alerts:
        payload = alert.payload or {}
        if payload.get("token"):
            flow_trades.append({
                "token_symbol": payload.get("token"), "side": payload.get("side"),
                "status": "alert", "size_usd": payload.get("usdValue"),
                "realized_pnl_usd": payload.get("realizedPnlUsd"),
                "captured_at": alert.created_at,
            })
    live_scores = {item["id"]: item["whale_score"] for item in rows}
    live_handles = {item["id"]: item["handle"] for item in rows}
    tokens = _token_rankings(
        token_trades, live_scores, live_handles,
        include_mock=get_settings().data_provider == "mock",
    )
    try:
        market = await token_market_data(tokens, get_settings())
        for token in tokens:
            platform = {"ethereum": "ethereum", "base": "base", "bsc": "binance-smart-chain", "solana": "solana"}.get(token.get("chain"))
            values = market.get((platform, str(token.get("address", "")).lower()), {}) if platform else {}
            token["market_status"] = "ready" if values.get("usd") is not None else "updating"
            token["price_usd"] = values.get("usd")
            token["market_cap_usd"] = values.get("usd_market_cap")
            token["volume_24h_usd"] = values.get("usd_24h_vol")
            token["change_24h"] = values.get("usd_24h_change")
            if token.get("sell_volume_usd") == 0 and token.get("transfer_quantity") and values.get("usd"):
                token["sell_volume_usd"] = round(token["transfer_quantity"] * values["usd"], 2)
    except Exception:
        pass
    # Price the size-less on-chain transfers from live market data, then keep
    # only the whale-sized events in the flow feed.
    price_by_token: dict[str, float] = {
        str(item.get("address", "")).lower(): item["price_usd"]
        for item in tokens if item.get("address") and isinstance(item.get("price_usd"), (int, float))
    }
    flow_min = get_settings().flow_min_usd
    priced_flow: list[dict] = []
    for row in flow_trades:
        size = row.get("size_usd")
        if (size is None or float(size) == 0) and row.get("quantity"):
            address = str(row.get("token_address") or "").lower()
            price = price_by_token.get(address)
            if price:
                row["size_usd"] = round(float(row["quantity"]) * price, 2)
        if float(row.get("size_usd") or 0) >= flow_min:
            priced_flow.append(row)
    flow_trades = priced_flow
    return {
        "updated_at": datetime.now(UTC),
        "rows": rows[:limit],
        "alerts": [{"type": item.alert_type, "created_at": item.created_at,
                    "payload": item.payload} for item in alerts],
        "flow": flow_summary(flow_trades),
        "tokens": tokens,
        "kpis": {
            "volume_24h": round(trade_volume, 2) if trade_volume else None,
            "leaderboard_volume_24h": round(leaderboard_volume, 2),
            "top_whale": top_whale["handle"] if top_whale else None,
            "top_whale_score": top_whale["whale_score"] if top_whale else None,
            "trade_events_24h": len(classified_trades),
            "hot_token": token_counts.most_common(1)[0][0] if token_counts else None,
            "traders": len(rows), "api_status": "LIVE",
        },
        "status": {
            "worker": last_run.status.upper() if last_run else "IDLE",
            "database": "CONNECTED", "api": "LIVE",
            "last_collection": last_run.finished_at if last_run else None,
        },
    }


def _token_rankings(
    trades: list[Trade], scores: dict[int, float], handles: dict[int, str],
    *, include_mock: bool = False,
) -> list[dict]:
    grouped: dict[str, dict] = {}
    for trade in trades:
        token = trade.token_symbol or trade.token_address
        if not token:
            continue
        if token.upper() == "MOCK" and not include_mock:
            continue
        item = grouped.setdefault(token, {
            "token": token, "address": trade.token_address, "chain": trade.chain, "sell_events": 0,
            "transfer_events": 0, "sell_volume_usd": 0.0, "transfer_quantity": 0.0,
            "traders": set(), "whale_score_total": 0.0, "whale_scores": {},
        })
        item["transfer_events"] += 1
        item["traders"].add(trade.trader_id)
        score = scores.get(trade.trader_id)
        if score is not None:
            item["whale_score_total"] += float(score)
            item["whale_scores"][trade.trader_id] = float(score)
        side = str(trade.side or "").lower()
        if side in {"sell", "sold", "sent", "outgoing"}:
            item["sell_events"] += 1
            if trade.size_usd is not None:
                item["sell_volume_usd"] += float(trade.size_usd)
        if trade.quantity is not None:
            item["transfer_quantity"] += float(trade.quantity)
    result = []
    for item in grouped.values():
        # Do not show a fake zero row when the chain event has no decoded amount.
        if item["sell_events"] == 0 and item["transfer_quantity"] == 0 and item["sell_volume_usd"] == 0:
            continue
        item["trader_count"] = len(item.pop("traders"))
        scores_for_token = item.pop("whale_scores")
        top_id, top_score = max(scores_for_token.items(), key=lambda pair: pair[1], default=(None, None))
        item["top_whale"] = handles.get(top_id) if top_id is not None else None
        item["top_whale_score"] = round(top_score, 2) if top_score is not None else None
        item["sell_volume_usd"] = round(item["sell_volume_usd"], 2)
        item["transfer_quantity"] = round(item["transfer_quantity"], 8)
        item["whale_score_total"] = round(item["whale_score_total"], 2)
        result.append(item)
    result.sort(key=lambda item: (item["sell_volume_usd"] or item["sell_events"], item["trader_count"]), reverse=True)
    return result[:100]
