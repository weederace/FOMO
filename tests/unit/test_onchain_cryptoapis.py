"""Unit tests for the CryptoAPIs path of the on-chain scanner."""

import asyncio
from datetime import UTC, datetime

from app.config.settings import Settings
from app.providers.onchain import OnchainScanner


def _settings(**overrides) -> Settings:
    base = dict(
        chain_scan_enabled=True,
        cryptoapis_api_key="test-cryptoapis-key",
        eth_daily_request_limit=10_000,
        evm_requests_per_second=1_000,
        solana_monthly_request_limit=10_000_000,
        solana_requests_per_second=1_000,
    )
    base.update(overrides)
    return Settings(**base)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _cryptoapis_payload():
    """Two transfers in one tx (in + out) → dex_like BUY/SELL, plus one plain
    inbound transfer → RECEIVED."""
    return {"data": {"items": [
        {
            "transactionHash": "0xabc",
            "sender": "0xwallet",
            "fungibleValues": {"amount": "1.5"},
            "timestamp": 1_700_000_000,
            "tokenData": {"symbol": "USDC", "contractAddress": "0xtoken"},
        },
        {
            "transactionHash": "0xabc",
            "sender": "0xother",
            "fungibleValues": {"amount": "2.5"},
            "timestamp": 1_700_000_000,
            "tokenData": {"symbol": "USDC", "contractAddress": "0xtoken"},
        },
        {
            "transactionHash": "0xdef",
            "sender": "0xsomeone",
            "fungibleValues": {"amount": "0.25"},
            "timestamp": 1_700_000_100,
            "tokenData": {"symbol": "AERO", "contractAddress": "0xaero"},
        },
    ]}}


def test_cryptoapis_scans_base_and_bsc_before_etherscan(monkeypatch):
    """With a CryptoAPIs key, base/bsc go through CryptoAPIs; a failing call
    falls back to the Etherscan path instead of losing the wallet."""
    scanner = OnchainScanner(_settings(etherscan_api_key="etherscan-key"))
    seen: list[str] = []

    # Split the real method: mock only the HTTP fetch per host, keep the real
    # parsing so the payload→NormalizedTrade mapping stays under test.
    async def fake_get_json(self, url, params, headers, quota):
        if "rest.cryptoapis.io" in url:
            assert headers["X-API-Key"] == "test-cryptoapis-key"
            return _cryptoapis_payload()
        seen.append("etherscan")
        assert "api.etherscan.io" in url
        assert params["apikey"] == "etherscan-key"
        return {"result": []}

    real_scan = OnchainScanner._scan_cryptoapis_evm

    async def patched_scan(self, chain, trader_id, wallet):
        seen.append(chain)
        if chain == "base":
            return await real_scan(self, chain, trader_id, wallet)
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(OnchainScanner, "_get_json", fake_get_json)
    monkeypatch.setattr(OnchainScanner, "_scan_cryptoapis_evm", patched_scan)
    trades = asyncio.run(scanner.scan_wallet("t1", "0xWallet"))

    # iteration order: ethereum (etherscan), base (cryptoapis), bsc (fails→
    # etherscan fallback). Every chain gets exactly one Etherscan leg except
    # base which CryptoAPIs served; 3 etherscan calls total (eth, bsc-fallback
    # happens via `continue` so only eth + bsc legs → wait: bsc fails and falls
    # through to etherscan, so eth + bsc = 2 etherscan calls).
    assert seen.count("etherscan") == 2
    assert seen[0] == "etherscan" and seen[1] == "base" and seen[2] == "bsc"
    assert seen[-1] == "etherscan"
    assert [t.side for t in trades] == ["SELL", "BUY", "RECEIVED"]
    assert trades[0].platform == "base-cryptoapis"
    assert str(trades[0].quantity) == "1.5"
    assert trades[0].executed_at == datetime.fromtimestamp(1_700_000_000, UTC)


def test_cryptoapis_without_key_skips_to_etherscan_only():
    scanner = OnchainScanner(_settings(cryptoapis_api_key=None, etherscan_api_key="etherscan-key"))

    async def fail_scan(self, chain, trader_id, wallet):
        raise AssertionError("cryptoapis path must not run without a key")

    scanner._scan_cryptoapis_evm = fail_scan.__get__(scanner)  # type: ignore[method-assign]
    trades = asyncio.run(scanner.scan_wallet("t1", "0xWallet"))
    assert trades == []


def test_no_keys_at_all_returns_empty():
    scanner = OnchainScanner(_settings(cryptoapis_api_key=None, etherscan_api_key=None))
    assert asyncio.run(scanner.scan_wallet("t1", "0xWallet")) == []
