"""Tests for the gmgn-cli subprocess wrapper (no real CLI required)."""

import asyncio
import json
from decimal import Decimal

import pytest

import app.providers.gmgn as gmgn_module
from app.providers.gmgn import (
    MIN_REQUEST_SPACING_SECONDS,
    GmgnClient,
    GmgnProviderError,
    build_wallet_stats,
)
from app.services.gmgn_service import wallet_score


class FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = None
        self._result = (returncode, stdout, stderr)

    async def communicate(self) -> tuple[bytes, bytes]:
        self.returncode = self._result[0]
        return self._result[1], self._result[2]

    def kill(self) -> None:  # pragma: no cover - only hit on timeouts
        pass

    async def wait(self) -> int:
        return self._result[0]


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch):
    """Replace subprocess spawning and PATH lookups.

    Returns (commands, responder): assign `responder[0] = factory` where
    factory(command) -> FakeProcess to control what the fake CLI prints.
    `commands` entries are (argv, kwargs) so tests can assert spawn options.
    """
    commands: list[tuple[list[str], dict]] = []
    responder: list = [lambda command: FakeProcess(0, b"[]")]

    async def fake_exec(*command: str, **kwargs) -> FakeProcess:
        commands.append((list(command), kwargs))
        return responder[0](command)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # On Windows `_resolve_command` turns the name into a full path; emulate it
    # so assertions can expect the plain name regardless of platform.
    monkeypatch.setattr(gmgn_module.os, "name", "posix")
    monkeypatch.setattr(gmgn_module.shutil, "which", lambda name: True)
    return commands, responder


def payload_factory(payload) -> object:
    encoded = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return lambda _command: FakeProcess(0, encoded)


async def test_raw_parses_json_and_appends_raw_flag(spawn):
    commands, responder = spawn
    responder[0] = payload_factory({"data": {"rank": [{"symbol": "WIF"}]}})

    client = GmgnClient()
    result = await client.raw("market", "trending", "--chain", "sol")

    assert result == {"data": {"rank": [{"symbol": "WIF"}]}}
    argv, kwargs = commands[0]
    assert argv[-1] == "--raw"
    assert argv[:3] == ["gmgn-cli", "market", "trending"]
    assert kwargs.get("creationflags") == gmgn_module.NO_WINDOW
    assert client.request_metrics() == {
        "request_count": 1, "success_count": 1, "error_count": 0, "last_exit_code": 0,
    }


async def test_raw_raises_clean_error_on_auth_failure(spawn):
    responder = spawn[1]
    responder[0] = lambda _command: FakeProcess(1, b"", b"error: 401 unauthorized")

    client = GmgnClient()
    with pytest.raises(GmgnProviderError) as excinfo:
        await client.raw("market", "trending")

    message = str(excinfo.value)
    assert "GMGN_API_KEY" in message and "gmgn.ai/ai" in message
    assert client.error_count == 1 and client.success_count == 0


async def test_raw_includes_stderr_tail_on_generic_failure(spawn):
    responder = spawn[1]
    responder[0] = lambda _command: FakeProcess(2, b"", b"boom went the command")

    client = GmgnClient()
    with pytest.raises(GmgnProviderError, match="boom went the command"):
        await client.raw("market", "hot-searches")


async def test_raw_rejects_unreadable_output(spawn):
    responder = spawn[1]
    responder[0] = lambda _command: FakeProcess(0, b"not json {")

    client = GmgnClient()
    with pytest.raises(GmgnProviderError, match="unreadable output"):
        await client.raw("market", "trending")
    assert client.error_count == 1


async def test_error_message_never_leaks_api_key(spawn):
    secret = "gmgn_live_SUPERSECRET123"
    responder = spawn[1]
    responder[0] = lambda _command: FakeProcess(1, b"", f"request failed with key {secret}: 403".encode())

    client = GmgnClient()
    with pytest.raises(GmgnProviderError) as excinfo:
        await client.raw("market", "trending")

    assert secret not in str(excinfo.value)


async def test_missing_cli_without_auto_install_fails_fast(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gmgn_module.shutil, "which", lambda name: None)

    client = GmgnClient(auto_install=False)
    with pytest.raises(GmgnProviderError, match="not found on PATH"):
        await client.raw("market", "trending")


async def test_auto_install_attempts_npm_once_then_fails_clean(monkeypatch: pytest.MonkeyPatch):
    commands: list[list[str]] = []

    async def fake_exec(*command: str, **_kwargs) -> FakeProcess:
        commands.append(list(command))
        return FakeProcess(0, b"added 1 package")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    # npm exists on PATH, but the CLI never shows up after installing.
    monkeypatch.setattr(gmgn_module.shutil, "which", lambda name: "npm" if name == "npm" else None)

    client = GmgnClient()
    with pytest.raises(GmgnProviderError, match="still missing"):
        await client.raw("market", "trending")
    with pytest.raises(GmgnProviderError, match="still missing"):
        await client.raw("market", "trending")

    npm_calls = [command for command in commands if command[0] == "npm"]
    assert len(npm_calls) == 1 and "gmgn-cli" in npm_calls[0]


async def test_rate_limit_serializes_concurrent_calls(spawn):
    commands, _responder = spawn
    client = GmgnClient(min_spacing_seconds=0.02)

    await asyncio.gather(*(client.raw("market", "trending") for _ in range(4)))

    assert client.success_count == 4
    assert len(commands) == 4
    assert MIN_REQUEST_SPACING_SECONDS > 1  # production default stays at ~1 rps


async def test_market_trending_unwraps_rows_and_builds_flags(spawn):
    commands, responder = spawn
    responder[0] = payload_factory({"data": {"rank": [{"symbol": "WIF"}, {"symbol": "BONK"}]}})

    client = GmgnClient()
    rows = await client.market_trending("sol", "5m", limit=2, min_market_cap=1000)

    assert [row["symbol"] for row in rows] == ["WIF", "BONK"]
    argv, _kwargs = commands[0]
    assert "--min-market-cap" in argv and "1000" in argv


async def test_market_trenches_maps_buckets(spawn):
    responder = spawn[1]
    responder[0] = payload_factory({
        "data": {"new_creation": [{"symbol": "NEW"}], "pump": [{"symbol": "PUMP"}], "completed": []}
    })

    client = GmgnClient()
    buckets = await client.market_trenches("sol")

    assert [row["symbol"] for row in buckets["new_creation"]] == ["NEW"]
    assert [row["symbol"] for row in buckets["near_completion"]] == ["PUMP"]
    assert buckets["completed"] == []


async def test_hot_searches_unwraps_interval_blocks(spawn):
    """The CLI returns [{interval, chain, tokens: [...]}] blocks — the client
    must flatten the nested token rows or the Hot Searches tab shows blanks."""
    responder = spawn[1]
    responder[0] = payload_factory([
        {"interval": "5m", "chain": "sol", "tokens": [{"symbol": "STONK"}, {"symbol": "CRIMECAT"}]},
        {"interval": "5m", "chain": "eth", "tokens": [{"symbol": "STOCKER"}]},
    ])

    client = GmgnClient()
    rows = await client.market_hot_searches("5m", limit=10)

    assert [row["symbol"] for row in rows] == ["STONK", "CRIMECAT", "STOCKER"]


async def test_portfolio_stats_reads_nested_payload(spawn):
    responder = spawn[1]
    responder[0] = payload_factory({
        "data": {
            "walletA": {
                "30d": {
                    "realized_profit": "1250.5", "win_rate": 0.63, "pnl_multiplier": 2.4,
                    "buy": 41, "sell": 39, "total_spent": "250000",
                }
            }
        }
    })

    client = GmgnClient()
    stats = await client.portfolio_stats("sol", ["walletA"], period="30d")

    assert stats[0].realized_profit_usd == Decimal("1250.5")
    assert stats[0].win_rate == pytest.approx(0.63)
    assert stats[0].buy_count == 41 and stats[0].sell_count == 39
    assert stats[0].total_spent_usd == Decimal("250000")


async def test_batch_stats_maps_every_wallet(spawn):
    responder = spawn[1]
    responder[0] = payload_factory({
        "data": {"w1": {"30d": {"realized_profit": 10}}, "w2": {"30d": {"realized_profit": -5}}}
    })

    client = GmgnClient()
    stats = await client.portfolio_stats("sol", ["w1", "w2"])

    assert [item.wallet for item in stats] == ["w1", "w2"]
    assert float(stats[0].realized_profit_usd) == 10
    assert float(stats[1].realized_profit_usd) == -5


async def test_wallet_score_handles_both_win_rate_scales():
    proven = build_wallet_stats("sol", "w", {"data": {
        "w": {"30d": {"realized_profit": 900, "win_rate": 0.63, "pnl_multiplier": 2.4,
                       "buy": 41, "sell": 39, "total_spent": "250000"}}
    }}, "30d")
    score = wallet_score(proven)
    assert score["verdict"].startswith("PROVEN")
    assert score["score"] == 85  # multiplier >= 2
    assert score["style"] == "large-size"
    assert score["win_rate"] == pytest.approx(63)  # 0..1 normalized to percent

    already_percent = build_wallet_stats("sol", "w", {"data": {"w": {"30d": {"win_rate": 72}}}}, "30d")
    assert wallet_score(already_percent)["win_rate"] == pytest.approx(72)  # not doubled

    losing = build_wallet_stats("sol", "w", {"data": {"w": {"30d": {"realized_profit": -100}}}}, "30d")
    assert wallet_score(losing)["score"] == 25
