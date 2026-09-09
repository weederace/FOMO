"""Integration tests for the /gmgn/* API endpoints (CLI faked, cache seeded)."""


import pytest
from fastapi.testclient import TestClient

import app.api.main as api_main
import app.providers.gmgn as gmgn_module
from app.api.main import app
from app.services.gmgn_market import GmgnMarketCache, _token_row, market_cache

TRENDING_ROW = {
    "address": "mint-wif",
    "token": {"symbol": "WIF", "address": "mint-wif"},
    "market_cap": 2_410_000_000,
    "liquidity": 41_200_000,
    "volume": 118_400_000,
    "holder_count": 214_000,
    "smart_degen_count": 34,
    "renowned_count": 5,
    "price": 2.41,
    "price_change_5m": 1.8,
}


@pytest.fixture
def gmgn_env(monkeypatch: pytest.MonkeyPatch):
    """Enable GMGN, reset the market cache, and fake the CLI subprocess layer."""
    from app.config.settings import Settings

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", gmgn_enabled=True)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    cache = GmgnMarketCache(ttl_seconds=60)
    monkeypatch.setattr("app.api.main._cache", cache) if False else None
    # the API imports the shared cache object; replace its contents instead
    shared = market_cache()
    monkeypatch.setattr(shared, "_store", {})
    monkeypatch.setattr(shared, "ttl_seconds", 60)

    async def fake_exec(*command: str, **_kwargs):
        class P:
            returncode = None

            async def communicate(self):
                self.returncode = 0
                import json

                return json.dumps({"data": {"rank": [TRENDING_ROW]}}).encode(), b""

            def kill(self):
                pass

            async def wait(self):
                return 0

        return P()

    monkeypatch.setattr(gmgn_module.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(gmgn_module.shutil, "which", lambda name: name or None)
    return cache


def test_gmgn_endpoints_404_when_disabled(monkeypatch: pytest.MonkeyPatch):
    from app.config.settings import Settings

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", gmgn_enabled=False)
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    with TestClient(app) as client:
        for path in ("/gmgn/trending", "/gmgn/trenches", "/gmgn/hot-searches"):
            assert client.get(path).status_code == 404

def test_gmgn_status_reports_setup():
    with TestClient(app) as client:
        response = client.get("/gmgn/status")
    assert response.status_code == 200
    payload = response.json()
    assert "enabled" in payload and "cli_found" in payload and "api_key_set" in payload
    assert payload["supported_chains"] == ["sol", "bsc", "base", "eth"]


def test_trending_normalizes_and_caches(gmgn_env):
    with TestClient(app) as client:
        first = client.get("/gmgn/trending?interval=5m")
        assert first.status_code == 200
        assert first.json()["source"] == "cli"
        rows = first.json()["rows"]
        assert rows[0]["symbol"] == "WIF"
        assert rows[0]["market_cap_usd"] == pytest.approx(2_410_000_000)
        assert rows[0]["smart_degen_count"] == 34

        second = client.get("/gmgn/trending?interval=5m")
        assert second.json()["source"] == "cache"  # served without invoking the CLI again


def test_hot_searches_uses_cache_key_per_interval(gmgn_env):
    with TestClient(app) as client:
        response = client.get("/gmgn/hot-searches?interval=1h")
        assert response.status_code == 200
        assert response.json()["source"] == "cli"
        assert market_cache().get("hot_searches:1h") is not None


def test_trenches_merges_bucket_shapes(gmgn_env):
    with TestClient(app) as client:
        response = client.get("/gmgn/trenches")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["new_creation"]) == set()  # faked CLI returned an empty rank list
    assert payload["source"] == "cli"


def test_token_row_flattens_gmgn_fields():
    row = _token_row("sol", TRENDING_ROW)
    assert row == {
        "chain": "sol", "symbol": "WIF", "address": "mint-wif",
        "market_cap_usd": 2_410_000_000, "liquidity_usd": 41_200_000,
        "volume_usd": 118_400_000, "swaps": None, "holders": 214_000,
        "smart_degen_count": 34, "renowned_count": 5, "price_usd": 2.41,
        "change_5m": 1.8, "change_1h": None, "launchpad": None, "created_at": None,
    }
    # no Decimal leaks: the payload must be JSON-serializable as-is
    import json

    json.dumps(row)


def test_token_row_falls_back_to_token_name_for_symbol():
    """Some GMGN rows ship only `token.name` (no symbol) — the UI must still
    get a label instead of an empty token column."""
    row = _token_row("sol", {"address": "mint-x", "token": {"name": "Treecoin"}})
    assert row["symbol"] == "Treecoin"
    row = _token_row("sol", {"address": "mint-y", "name": "Pepe CEO"})
    assert row["symbol"] == "Pepe CEO"


def test_alert_event_id_is_stable_and_type_scoped():
    from app.services.gmgn_market import alert_event_id

    one = alert_event_id("GMGN_TRENDING", "sol", "mint-wif")
    again = alert_event_id("GMGN_TRENDING", "sol", "mint-wif")
    other_type = alert_event_id("GMGN_NEW_TOKEN", "sol", "mint-wif")
    assert one == again and one != other_type
    assert len(one) == 16
