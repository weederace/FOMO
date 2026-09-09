import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.providers.base import BaseProvider
from app.schemas.trader import NormalizedBalance, NormalizedTrade, NormalizedTrader

LOGGER = logging.getLogger(__name__)


class FomoProviderError(RuntimeError):
    """A safe, provider-specific error without credentials or response secrets."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LeaderboardRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rank: int | None = Field(default=None, ge=1)
    handle: str
    displayName: str | None = None
    pnlUsd: Decimal | None = None
    volumeUsd: Decimal | None = None
    trades: int | None = Field(default=None, ge=0)
    followers: int | None = Field(default=None, ge=0)
    wallets: dict[str, str | None] | None = None


class LeaderboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capturedAt: datetime | None = None
    traders: list[LeaderboardRow] = Field(default_factory=list)


class TraderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    handle: str
    displayName: str | None = None
    pnlUsd: Decimal | None = None
    volumeUsd: Decimal | None = None
    trades: int | None = None
    numTrades: int | None = None
    followers: int | None = None
    wallets: dict[str, str | None] | None = None


class TradeRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tradeId: str | None = None
    token: dict[str, Any] | None = None
    amount: Decimal | None = None
    avgEntryPrice: Decimal | None = None
    avgExitPrice: Decimal | None = None
    realizedPnlUsd: Decimal | None = None
    unrealizedPnlUsd: Decimal | None = None
    status: str | None = None
    side: str | None = None
    chain: Any = None
    closedAt: Any = None
    createdAt: Any = None


class TradesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    available: bool | None = None
    trades: list[TradeRow] = Field(default_factory=list)


class BalanceRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: dict[str, Any] | None = None
    chain: Any = None
    amount: Decimal | None = None
    valueUsd: Decimal | None = None
    priceUsd: Decimal | None = None


class BalancesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    available: bool | None = None
    holdings: list[BalanceRow] = Field(default_factory=list)


class AlertRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    eventId: str | None = None
    type: str
    trader: str | None = None
    token: str | None = None
    tokenAddress: str | None = None
    chain: str | None = None
    chainId: int | None = None
    usdValue: Decimal | None = None
    text: str | None = None
    ts: int | None = None


class AlertsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    alerts: list[AlertRow] = Field(default_factory=list)


def parse_provider_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


class FomoProvider(BaseProvider):
    """Documented FOMOAPI adapter for public FOMO-derived trader data."""

    name = "fomoapi"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 20,
        rate_limit_per_second: float = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout_seconds)
        self.rate_limit = max(rate_limit_per_second, 0.01)
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0
        self._client: httpx.AsyncClient | None = None
        self.request_count = 0
        self.success_count = 0
        self.error_count = 0
        self.last_status_code: int | None = None
        self._credits_exhausted = False

    def capabilities(self) -> dict[str, bool]:
        detail_available = bool(self.api_key) and not self._credits_exhausted
        return {
            "leaderboard": True,
            "user_profile": detail_available,
            "trades": detail_available,
            "balances": detail_available,
            "alerts": True,
            "realtime": False,
        }

    async def _request(
        self, path: str, params: dict[str, Any] | None = None, authenticated: bool = True
    ) -> Any:
        if authenticated and self._credits_exhausted:
            raise FomoProviderError("FOMOAPI credits are exhausted", 402)
        if self._client is None:
            # Provider traffic must not inherit an invalid system proxy such as a
            # SOCKS URL when httpx was installed without SOCKS support.
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                trust_env=False,
            )
        headers = {"accept": "application/json"}
        if self.api_key and authenticated:
            headers["authorization"] = f"Bearer {self.api_key}"
        for attempt in range(3):
            async with self._rate_lock:
                if authenticated and self._credits_exhausted:
                    raise FomoProviderError("FOMOAPI credits are exhausted", 402)
                loop = asyncio.get_running_loop()
                wait = 1 / self.rate_limit - (loop.time() - self._last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request = loop.time()
            try:
                self.request_count += 1
                response = await self._client.get(path, params=params, headers=headers)
                self.last_status_code = response.status_code
            except httpx.HTTPError as exc:
                self.error_count += 1
                if attempt == 2:
                    raise FomoProviderError(f"FOMOAPI network failure: {type(exc).__name__}") from exc
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                self.error_count += 1
                if response.status_code == 402:
                    self._credits_exhausted = True
                    raise FomoProviderError("FOMOAPI credits are exhausted", 402)
                if response.status_code == 401:
                    raise FomoProviderError("FOMOAPI rejected the API key", response.status_code)
                if response.status_code == 404:
                    raise FomoProviderError("FOMOAPI resource was not found", response.status_code)
                raise FomoProviderError(f"FOMOAPI returned HTTP {response.status_code}", response.status_code)
            try:
                payload = response.json()
                self.success_count += 1
                return payload
            except ValueError as exc:
                raise FomoProviderError("FOMOAPI returned invalid JSON") from exc
        raise FomoProviderError("FOMOAPI request failed after retries")

    def request_metrics(self) -> dict[str, int | None]:
        return {
            "request_count": self.request_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_status_code": self.last_status_code,
        }

    async def get_leaderboard(self, window: str = "24h", limit: int = 150) -> list[NormalizedTrader]:
        if window not in {"24h", "7d", "30d", "all"}:
            raise ValueError("window must be one of 24h, 7d, 30d, all")
        payload = LeaderboardResponse.model_validate(
            await self._request(f"/v2/leaderboard/{window}", {"limit": min(limit, 150)}, authenticated=False)
        )
        captured_at = payload.capturedAt or datetime.now(UTC)
        return [
            NormalizedTrader(
                platform=self.name,
                platform_trader_id=row.handle,
                username=row.handle,
                display_name=row.displayName,
                profile_url=f"https://fomo.family/profile/{row.handle}",
                wallet_address=(row.wallets or {}).get("evm") or (row.wallets or {}).get("solana"),
                wallets={key: value for key, value in (row.wallets or {}).items() if value},
                rank=row.rank,
                pnl=row.pnlUsd,
                volume=row.volumeUsd,
                follower_count=row.followers,
                total_trades=row.trades,
                captured_at=captured_at,
            )
            for row in payload.traders
        ]

    async def get_trader(self, platform_trader_id: str) -> NormalizedTrader:
        try:
            payload = TraderResponse.model_validate(
                await self._request(f"/v2/users/{platform_trader_id}")
            )
            return NormalizedTrader(
                platform=self.name,
                platform_trader_id=payload.handle,
                username=payload.handle,
                display_name=payload.displayName,
                profile_url=f"https://fomo.family/profile/{payload.handle}",
                wallet_address=(payload.wallets or {}).get("evm") or (payload.wallets or {}).get("solana"),
                wallets={key: value for key, value in (payload.wallets or {}).items() if value},
                pnl=payload.pnlUsd,
                volume=payload.volumeUsd,
                follower_count=payload.followers,
                total_trades=payload.trades or payload.numTrades,
                captured_at=datetime.now(UTC),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise FomoProviderError("FOMOAPI trader response did not match the documented shape") from exc

    async def get_trades(self, platform_trader_id: str) -> list[NormalizedTrade]:
        try:
            payload = TradesResponse.model_validate(
                await self._request(f"/v2/users/{platform_trader_id}/trades", {"limit": 25})
            )
        except ValidationError as exc:
            raise FomoProviderError("FOMOAPI trades response did not match the documented shape") from exc
        if payload.available is False:
            return []
        captured_at = datetime.now(UTC)
        result: list[NormalizedTrade] = []
        for raw in payload.trades:
            token = raw.token or {}
            created_at = parse_provider_datetime(raw.createdAt)
            closed_at = parse_provider_datetime(raw.closedAt)
            result.append(
                NormalizedTrade(
                    platform=self.name,
                    trader_platform_id=platform_trader_id,
                    platform_trade_id=raw.tradeId,
                    token_symbol=token.get("symbol"),
                    token_address=token.get("address"),
                    chain=str(raw.chain) if raw.chain is not None else None,
                    side=raw.side,
                    size_usd=raw.amount,
                    price=raw.avgEntryPrice,
                    status=raw.status,
                    realized_pnl_usd=raw.realizedPnlUsd,
                    unrealized_pnl_usd=raw.unrealizedPnlUsd,
                    holding_duration_seconds=(int((closed_at - created_at).total_seconds()) if closed_at and created_at else None),
                    executed_at=created_at,
                    captured_at=captured_at,
                )
            )
        return result

    async def get_balances(self, platform_trader_id: str) -> list[NormalizedBalance]:
        if not self.api_key:
            raise FomoProviderError("FOMOAPI balances require FOMO_API_KEY")
        try:
            payload = BalancesResponse.model_validate(
                await self._request(f"/v2/users/{platform_trader_id}/balances")
            )
        except ValidationError as exc:
            raise FomoProviderError("FOMOAPI balances response did not match the documented shape") from exc
        if payload.available is False:
            return []
        captured_at = datetime.now(UTC)
        return [
            NormalizedBalance(
                platform=self.name,
                trader_platform_id=platform_trader_id,
                token_symbol=(row.token or {}).get("symbol"),
                token_address=(row.token or {}).get("address"),
                chain=str(row.chain) if row.chain is not None else None,
                amount=row.amount,
                value_usd=row.valueUsd,
                captured_at=captured_at,
            )
            for row in payload.holdings
        ]

    async def get_alerts(self, limit: int = 50) -> list[dict]:
        payload = AlertsResponse.model_validate(
            await self._request("/v2/alerts", {"limit": min(limit, 100)}, authenticated=False)
        )
        result = []
        for row in payload.alerts:
            item = row.model_dump(mode="json", exclude_none=True)
            if "usdValue" in item:
                item["usdValue"] = float(item["usdValue"])
            result.append(item)
        return result

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
