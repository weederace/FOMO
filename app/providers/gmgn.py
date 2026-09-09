"""Async wrapper around the `gmgn-cli` command-line tool.

GMGN exposes its read-only market and wallet intelligence through a Node CLI
(`npm install -g gmgn-cli`) that prints JSON to stdout and authenticates with
the `GMGN_API_KEY` environment variable. This module never trades: it only
invokes the read-only data commands and normalizes their output.

The CLI's documented default rate limit is roughly 1 request per second, so
every invocation is spaced out behind a lock regardless of the caller.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LOGGER = logging.getLogger(__name__)

GMGN_PLATFORM = "gmgn"
# The documented data-crawling ceiling is ~1 request per second; stay under it.
MIN_REQUEST_SPACING_SECONDS = 1.05
INSTALL_COMMAND = ("npm", "install", "-g", "gmgn-cli")
# gmgn-cli is a .cmd shim on Windows; without this flag every invocation
# flashes a console window — the worker spawns it dozens of times per cycle.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GmgnProviderError(RuntimeError):
    """The gmgn-cli tool is missing, failed, or returned unreadable data."""


def _resolve_command(name: str) -> str:
    """Resolve a command name to its full path so Windows can exec .cmd shims."""
    if os.name != "nt":
        return name
    resolved = shutil.which(name)
    return resolved or name


async def _run_process(command: tuple[str, ...], timeout_seconds: float) -> tuple[int, str, str]:
    argv = (command[0], *command[1:]) if os.name != "nt" else (_resolve_command(command[0]), *command[1:])
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise GmgnProviderError(f"`{command[0]}` timed out after {timeout_seconds:.0f}s") from None
    return (
        process.returncode if process.returncode is not None else -1,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def cli_available(command: str = "gmgn-cli") -> bool:
    """Whether the CLI binary can be found on PATH right now."""
    return shutil.which(command) is not None


async def install_cli(timeout_seconds: float = 300) -> None:
    """Download the CLI through npm. Idempotent, and quick once it is present."""
    if shutil.which("npm") is None:
        raise GmgnProviderError(
            "gmgn-cli is missing and npm was not found. Install Node.js from "
            "https://nodejs.org, or run `npm install -g gmgn-cli` manually."
        )
    returncode, stdout, stderr = await _run_process(INSTALL_COMMAND, timeout_seconds)
    tail = " ".join((stdout + " " + stderr).split())[-200:]
    if returncode != 0:
        raise GmgnProviderError(f"`npm install -g gmgn-cli` failed (exit {returncode}): {tail}")
    LOGGER.info("gmgn-cli installed through npm")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        number = _to_decimal(value)
        return int(number) if number is not None else None


def _to_float(value: Any) -> float | None:
    number = _to_decimal(value)
    return float(number) if number is not None else None


def _to_timestamp(value: Any) -> datetime | None:
    """CLI timestamps arrive as Unix seconds, milliseconds, or ISO strings."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return _to_timestamp(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:  # milliseconds
            seconds /= 1000
        if seconds <= 0:
            return None
        return datetime.fromtimestamp(seconds, tz=UTC)
    return None


class GmgnWalletStats(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chain: str | None = None
    wallet: str | None = None
    realized_profit_usd: Decimal | None = None
    unrealized_profit_usd: Decimal | None = None
    total_profit_usd: Decimal | None = None
    win_rate: float | None = None
    pnl_multiplier: float | None = None
    buy_count: int | None = None
    sell_count: int | None = None
    total_spent_usd: Decimal | None = None
    period: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def build_wallet_stats(chain: str, wallet: str, payload: Any, period: str | None = None) -> GmgnWalletStats:
    """Read one wallet's `portfolio stats` payload into the normalized shape.

    The CLI nests the numbers under period keys (e.g. `data["30d"]`) with
    several spellings per field, so this walks every known variant.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}
    data = data.get(wallet, data)  # batch responses are keyed by wallet
    if isinstance(data, dict) and period and isinstance(data.get(period), dict):
        data = data[period]

    def pick(*names: str) -> Any:
        for name in names:
            if isinstance(data, dict) and data.get(name) is not None:
                return data[name]
        return None

    return GmgnWalletStats(
        chain=chain,
        wallet=wallet,
        realized_profit_usd=_to_decimal(pick("realized_profit", "realizedProfit", "pnl", "realized_pnl")),
        unrealized_profit_usd=_to_decimal(pick("unrealized_profit", "unrealizedProfit")),
        total_profit_usd=_to_decimal(pick("total_profit", "totalProfit", "total_pnl")),
        win_rate=_to_float(pick("win_rate", "winRate", "winrate")),
        pnl_multiplier=_to_float(pick("pnl_multiplier", "pnlMultiplier", "multiplier", "pnl_ratio")),
        buy_count=_to_int(pick("buy", "buys", "buy_count", "buyCount")),
        sell_count=_to_int(pick("sell", "sells", "sell_count", "sellCount")),
        total_spent_usd=_to_decimal(pick("total_spent", "totalSpent", "spent", "cost")),
        period=period,
        raw=data if isinstance(data, dict) else {},
    )


class GmgnClient:
    """Invoke read-only gmgn-cli commands and return parsed JSON."""

    def __init__(
        self,
        command: str = "gmgn-cli",
        request_timeout_seconds: float = 60,
        min_spacing_seconds: float = MIN_REQUEST_SPACING_SECONDS,
        auto_install: bool = True,
    ) -> None:
        self.command = command
        self.request_timeout_seconds = request_timeout_seconds
        self.min_spacing_seconds = min_spacing_seconds
        self.auto_install = auto_install
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._install_attempted = False
        self.invocation_count = 0
        self.success_count = 0
        self.error_count = 0
        self.last_exit_code: int | None = None

    def request_metrics(self) -> dict[str, int | None]:
        return {
            "request_count": self.invocation_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_exit_code": self.last_exit_code,
        }

    async def aclose(self) -> None:
        """Nothing long-lived to tear down; kept for provider symmetry."""

    async def _respect_rate_limit(self) -> None:
        async with self._lock:
            wait = self._last_call + self.min_spacing_seconds - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def _ensure_installed(self) -> None:
        if await cli_available(self.command):
            return
        if not self.auto_install:
            raise GmgnProviderError(
                f"`{self.command}` was not found on PATH. Install it with "
                "`npm install -g gmgn-cli` or set GMGN_CLI_COMMAND."
            )
        if self._install_attempted:
            raise GmgnProviderError(
                f"`{self.command}` is still missing after the automatic install. "
                "Install it with `npm install -g gmgn-cli` and check GMGN_CLI_COMMAND."
            )
        self._install_attempted = True
        LOGGER.info("gmgn-cli was not found. Installing it through npm; this happens once.")
        await install_cli()
        if not await cli_available(self.command):
            raise GmgnProviderError(
                f"`{self.command}` is still missing after the automatic install. "
                "Install it with `npm install -g gmgn-cli` and check GMGN_CLI_COMMAND."
            )

    async def raw(self, *args: str) -> Any:
        """Run one read-only CLI command with `--raw` and return parsed JSON."""
        await self._ensure_installed()
        await self._respect_rate_limit()
        self.invocation_count += 1
        command = (self.command, *args, "--raw")
        returncode, stdout, stderr = await _run_process(command, self.request_timeout_seconds)
        self.last_exit_code = returncode
        if returncode != 0:
            self.error_count += 1
            tail = " ".join(stderr.split())[-200:]
            if "GMGN_API_KEY" in stderr or "api key" in stderr.lower() or "401" in stderr or "403" in stderr:
                raise GmgnProviderError(
                    "gmgn-cli rejected the request: set a valid GMGN_API_KEY "
                    "(free key at https://gmgn.ai/ai)."
                )
            raise GmgnProviderError(f"`gmgn-cli {' '.join(args)}` failed (exit {returncode}): {tail}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            self.error_count += 1
            raise GmgnProviderError(
                f"`gmgn-cli {' '.join(args)}` printed unreadable output: {exc}"
            ) from exc
        self.success_count += 1
        return payload

    # ---- market commands ----

    async def market_trending(
        self, chain: str = "sol", interval: str = "5m", limit: int = 30, **filters: Any
    ) -> list[dict]:
        args = ["market", "trending", "--chain", chain, "--interval", interval, "--limit", str(limit)]
        args.extend(self._range_flags(filters))
        payload = await self.raw(*args)
        rows = self._unwrap_list(payload)
        return [row for row in rows if isinstance(row, dict)]

    async def market_trenches(
        self, chain: str = "sol", types: tuple[str, ...] = ("new_creation", "near_completion", "completed"),
        launchpad: str | None = None, limit: int = 30,
    ) -> dict[str, list[dict]]:
        args = ["market", "trenches", "--chain", chain, "--limit", str(limit)]
        for kind in types:
            args.extend(["--type", kind])
        if launchpad:
            args.extend(["--launchpad-platform", launchpad])
        payload = await self.raw(*args)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        buckets: dict[str, list[dict]] = {}
        mapping = {"new_creation": "new_creation", "near_completion": "pump", "completed": "completed"}
        for kind, key in mapping.items():
            value = data.get(key, [])
            buckets[kind] = [row for row in (value if isinstance(value, list) else []) if isinstance(row, dict)]
        return buckets

    async def market_hot_searches(self, interval: str = "5m", limit: int = 30) -> list[dict]:
        payload = await self.raw("market", "hot-searches", "--interval", interval, "--limit", str(limit))
        # The CLI returns a list of {interval, chain, tokens: [...]} blocks —
        # the searchable rows live under each block's `tokens` key, not at the
        # top level (that's why the Hot Searches tab used to show blank names).
        rows = self._unwrap_list(payload)
        if rows and all(isinstance(row, dict) and isinstance(row.get("tokens"), list) for row in rows):
            rows = [token for block in rows for token in block["tokens"]]
        return [row for row in rows if isinstance(row, dict)]

    async def market_kline(self, chain: str, address: str, resolution: str = "1m") -> list[dict]:
        payload = await self.raw(
            "market", "kline", "--chain", chain, "--address", address, "--resolution", resolution
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("klines", data.get("list", [])) if isinstance(data, dict) else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    # ---- token commands ----

    async def token_info(self, chain: str, address: str) -> dict:
        payload = await self.raw("token", "info", "--chain", chain, "--address", address)
        return payload.get("data", payload) if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload

    async def token_security(self, chain: str, address: str) -> dict:
        payload = await self.raw("token", "security", "--chain", chain, "--address", address)
        return payload.get("data", payload) if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload

    async def token_pool(self, chain: str, address: str) -> dict:
        payload = await self.raw("token", "pool", "--chain", chain, "--address", address)
        return payload.get("data", payload) if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload

    async def token_holders(self, chain: str, address: str, limit: int = 100, tag: str | None = None) -> list[dict]:
        args = ["token", "holders", "--chain", chain, "--address", address, "--limit", str(limit)]
        if tag:
            args.extend(["--tag", tag])
        payload = await self.raw(*args)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("holders", data.get("list", data)) if isinstance(data, dict) else data
        return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]

    async def token_traders(self, chain: str, address: str, limit: int = 100) -> list[dict]:
        payload = await self.raw(
            "token", "traders", "--chain", chain, "--address", address, "--limit", str(limit)
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("traders", data.get("list", data)) if isinstance(data, dict) else data
        return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]

    # ---- portfolio commands ----

    async def portfolio_stats(self, chain: str, wallets: list[str], period: str = "30d") -> list[GmgnWalletStats]:
        """Batch-query wallet statistics. The CLI accepts repeatable --wallet flags."""
        if not wallets:
            return []
        args = ["portfolio", "stats", "--chain", chain, "--period", period]
        for wallet in wallets:
            args.extend(["--wallet", wallet])
        payload = await self.raw(*args)
        return [build_wallet_stats(chain, wallet, payload, period) for wallet in wallets]

    async def portfolio_holdings(self, chain: str, wallet: str, limit: int = 50) -> list[dict]:
        payload = await self.raw(
            "portfolio", "holdings", "--chain", chain, "--wallet", wallet, "--limit", str(limit)
        )
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("holdings", data.get("list", data)) if isinstance(data, dict) else data
        return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]

    async def portfolio_activity(
        self, chain: str, wallet: str, limit: int = 50, types: tuple[str, ...] = ("buy", "sell")
    ) -> list[dict]:
        args = ["portfolio", "activity", "--chain", chain, "--wallet", wallet, "--limit", str(limit)]
        for kind in types:
            args.extend(["--type", kind])
        payload = await self.raw(*args)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("activity", data.get("activities", data.get("list", data))) if isinstance(data, dict) else data
        return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]

    async def portfolio_created_tokens(self, chain: str, wallet: str) -> list[dict]:
        payload = await self.raw("portfolio", "created-tokens", "--chain", chain, "--wallet", wallet)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        rows = data.get("tokens", data.get("created_tokens", data.get("list", data))) if isinstance(data, dict) else data
        return [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]

    async def portfolio_token_balance(self, chain: str, wallet: str, token: str) -> dict:
        payload = await self.raw(
            "portfolio", "token-balance", "--chain", chain, "--wallet", wallet, "--token", token
        )
        return payload.get("data", payload) if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload

    # ---- helpers ----

    @staticmethod
    def _unwrap_list(payload: Any) -> list:
        """CLI list endpoints wrap rows under `data`, `data.list`, or `rank`."""
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("data", "rank", "list", "tokens", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for inner in ("list", "rank", "tokens", "rows"):
                    if isinstance(value.get(inner), list):
                        return value[inner]
        return []

    @staticmethod
    def _range_flags(filters: dict[str, Any]) -> list[str]:
        """Turn {min_marketcap: 1000} into ["--min-marketcap", "1000"]."""
        flags: list[str] = []
        for key, value in filters.items():
            if value is None:
                continue
            flags.extend([f"--{key.replace('_', '-')}", str(value)])
        return flags


def _api_key_present() -> bool:
    """Whether a GMGN API key is configured (process env or project .env)."""
    if os.environ.get("GMGN_API_KEY"):
        return True
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return False
    try:
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = raw.partition("=")
            if key.strip() == "GMGN_API_KEY" and value.strip():
                return True
    except OSError:
        return False
    return False


def describe_setup(command: str = "gmgn-cli") -> dict[str, Any]:
    """Cheap synchronous status probe for /gmgn/status and the launcher."""
    return {
        "cli_command": command,
        "cli_found": shutil.which(command) is not None,
        "api_key_set": _api_key_present(),
        "supported_chains": ["sol", "bsc", "base", "eth"],
    }


if __name__ == "__main__":  # manual smoke test: python -m app.providers.gmgn
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    async def _smoke() -> None:
        client = GmgnClient()
        print(describe_setup(client.command))
        if not await cli_available(client.command):
            print("gmgn-cli is not installed yet; it installs on first use.")
            return
        rows = await client.market_trending("sol", "5m", limit=3)
        print(json.dumps(rows, indent=2)[:2000])
        await client.aclose()

    asyncio.run(_smoke())
