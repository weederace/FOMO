"""Quota-aware wallet transfer readers backed by Etherscan and Solscan APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.config.settings import Settings
from app.providers.quota import RequestQuota
from app.schemas.trader import NormalizedTrade


class OnchainScanner:
    """Read EVM transfers through Etherscan V2 and Solana transfers through Solscan."""

    EVM_CHAINS = {"ethereum": "1", "base": "8453", "bsc": "56"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(settings.request_timeout_seconds)
        self.client: httpx.AsyncClient | None = None
        self.evm_quota = RequestQuota(settings.evm_requests_per_second, 86_400, settings.eth_daily_request_limit)
        self.solana_quota = RequestQuota(settings.solana_requests_per_second, 2_592_000, settings.solana_monthly_request_limit)

    async def _client_instance(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout, trust_env=False)
        return self.client

    async def _get_json(self, url: str, params: dict[str, Any], headers: dict[str, str], quota: RequestQuota) -> Any:
        await quota.acquire()
        client = await self._client_instance()
        for attempt in range(3):
            try:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)

    async def scan_wallet(self, trader_id: str, wallet: str) -> list[NormalizedTrade]:
        if not self.settings.chain_scan_enabled or not wallet:
            return []
        if wallet.lower().startswith("0x"):
            return await self._scan_evm(trader_id, wallet)
        return await self._scan_solana(trader_id, wallet)

    def _etherscan_key(self) -> str | None:
        if self.settings.etherscan_api_key:
            return self.settings.etherscan_api_key
        legacy = self.settings.eth_rpc_url or ""
        return legacy if not legacy.lower().startswith(("http://", "https://")) else None

    def _solscan_key(self) -> str | None:
        if self.settings.solscan_api_key:
            return self.settings.solscan_api_key
        legacy = self.settings.solana_rpc_url or ""
        return legacy if not legacy.lower().startswith(("http://", "https://")) else None

    async def _scan_evm(self, trader_id: str, wallet: str) -> list[NormalizedTrade]:
        api_key = self._etherscan_key()
        if not api_key and not self.settings.cryptoapis_api_key:
            return []
        rows: list[dict[str, Any]] = []
        crypto_trades: list[NormalizedTrade] = []
        for chain, chain_id in self.EVM_CHAINS.items():
            if chain in {"base", "bsc"} and self.settings.cryptoapis_api_key:
                try:
                    crypto_trades.extend(await self._scan_cryptoapis_evm(chain, trader_id, wallet))
                    continue
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"CryptoAPIs failed for {chain}, falling back: {e}")
            if not api_key:
                continue
            payload = await self._get_json(
                "https://api.etherscan.io/v2/api",
                {
                    "chainid": chain_id, "module": "account", "action": "tokentx",
                    "address": wallet, "page": 1, "offset": 100, "sort": "desc",
                    "apikey": api_key,
                },
                {"accept": "application/json"}, self.evm_quota,
            )
            result = payload.get("result", []) if isinstance(payload, dict) else []
            if isinstance(result, list):
                rows.extend([{**item, "chain": chain} for item in result])
        directions: dict[str, set[str]] = {}
        for item in rows:
            direction = "out" if str(item.get("from", "")).lower() == wallet.lower() else "in"
            directions.setdefault(str(item.get("hash", "")), set()).add(direction)
        result: list[NormalizedTrade] = []
        for item in rows:
            tx_hash = str(item.get("hash", ""))
            direction = "out" if str(item.get("from", "")).lower() == wallet.lower() else "in"
            dex_like = directions.get(tx_hash) == {"in", "out"}
            decimals = int(item.get("tokenDecimal") or 0)
            raw_value = Decimal(str(item.get("value") or "0"))
            quantity = raw_value / (Decimal(10) ** decimals) if decimals else raw_value
            result.append(NormalizedTrade(
                platform=f"{item.get('chain', 'evm')}-etherscan", trader_platform_id=trader_id,
                platform_trade_id=f"{tx_hash}:{item.get('contractAddress')}:{item.get('transactionIndex')}",
                token_symbol=item.get("tokenSymbol"), token_address=item.get("contractAddress"),
                chain=item.get("chain"), side=("SELL" if direction == "out" else "BUY") if dex_like
                else ("SENT" if direction == "out" else "RECEIVED"), quantity=quantity,
                status="dex_like" if dex_like else "erc20_transfer", transaction_hash=tx_hash,
                executed_at=datetime.fromtimestamp(int(item.get("timeStamp", 0)), UTC)
                if item.get("timeStamp") else None, captured_at=datetime.now(UTC),
            ))
        return [*crypto_trades, *result]

    async def _scan_cryptoapis_evm(self, chain: str, trader_id: str, wallet: str) -> list[NormalizedTrade]:
        blockchain = "base" if chain == "base" else "binance-smart-chain"
        payload = await self._get_json(
            f"https://rest.cryptoapis.io/addresses-latest/evm/{blockchain}/mainnet/{wallet}/tokens-transfers",
            {"limit": 50, "sortingOrder": "DESCENDING"},
            {"accept": "application/json", "content-type": "application/json",
             "X-API-Key": self.settings.cryptoapis_api_key or ""}, self.evm_quota,
        )
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        directions: dict[str, set[str]] = {}
        for item in items if isinstance(items, list) else []:
            tx_hash = str(item.get("transactionHash") or "")
            direction = "out" if str(item.get("sender", "")).lower() == wallet.lower() else "in"
            directions.setdefault(tx_hash, set()).add(direction)
        result: list[NormalizedTrade] = []
        for item in items if isinstance(items, list) else []:
            tx_hash = str(item.get("transactionHash") or "")
            direction = "out" if str(item.get("sender", "")).lower() == wallet.lower() else "in"
            dex_like = directions.get(tx_hash) == {"in", "out"}
            fungible = item.get("fungibleValues") or {}
            quantity = Decimal(str(fungible.get("amount") or "0"))
            timestamp = item.get("timestamp")
            result.append(NormalizedTrade(
                platform=f"{chain}-cryptoapis", trader_platform_id=trader_id,
                platform_trade_id=f"{tx_hash}:{item.get('tokenData', {}).get('contractAddress')}",
                token_symbol=(item.get("tokenData") or {}).get("symbol"),
                token_address=(item.get("tokenData") or {}).get("contractAddress"), chain=chain,
                side=("SELL" if direction == "out" else "BUY") if dex_like
                else ("SENT" if direction == "out" else "RECEIVED"), quantity=quantity,
                status="dex_like" if dex_like else "erc20_transfer", transaction_hash=tx_hash,
                executed_at=datetime.fromtimestamp(int(timestamp), UTC) if timestamp else None,
                captured_at=datetime.now(UTC),
            ))
        return result

    async def _scan_solana(self, trader_id: str, wallet: str) -> list[NormalizedTrade]:
        api_key = self._solscan_key()
        if not api_key:
            return []
        payload = await self._get_json(
            "https://pro-api.solscan.io/v2.0/account/transfer",
            {"address": wallet, "page": 1, "page_size": 100},
            {"accept": "application/json", "token": api_key}, self.solana_quota,
        )
        data = payload.get("data", []) if isinstance(payload, dict) else []
        result: list[NormalizedTrade] = []
        for item in data if isinstance(data, list) else []:
            direction = "out" if str(item.get("from_address", "")).lower() == wallet.lower() else "in"
            amount = Decimal(str(item.get("amount") or item.get("token_amount") or "0"))
            decimals = int(item.get("token_decimals") or item.get("decimals") or 0)
            quantity = amount / (Decimal(10) ** decimals) if decimals else amount
            signature = item.get("trans_id") or item.get("signature")
            result.append(NormalizedTrade(
                platform="solana-solscan", trader_platform_id=trader_id,
                platform_trade_id=f"{signature}:{item.get('token_address')}",
                token_symbol=item.get("token_symbol"), token_address=item.get("token_address"),
                chain="solana", side="SENT" if direction == "out" else "RECEIVED",
                quantity=quantity, status="token_transfer", transaction_hash=signature,
                captured_at=datetime.now(UTC),
            ))
        return result

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()
