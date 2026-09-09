"""Unit tests for the watchlist refresh service."""

from app.services.watchlist_service import _flatten_price_block, _is_moving


def test_flatten_price_block_computes_percent_changes():
    """`token info` nests a price block with per-window references; the
    flattened row must expose price, 5m/1h/24h changes and volumes."""
    row: dict = {}
    info = {"price": {
        "price": "0.22", "price_5m": "0.20", "price_1h": "0.10", "price_24h": "0.05",
        "volume_5m": "100", "volume_1h": "500", "volume_24h": "9000",
        "swaps_24h": 1234,
    }}
    _flatten_price_block(info, row)
    assert row["price_usd"] == 0.22
    assert row["change_5m"] == 10.0
    assert row["change_1h"] == 120.0
    assert row["change_24h"] == 340.0
    assert row["volume_5m"] == 100.0
    assert row["volume_1h"] == 500.0
    assert row["volume_usd"] == 9000.0
    assert row["swaps"] == 1234


def test_flatten_price_block_ignores_missing_block():
    row: dict = {}
    _flatten_price_block({}, row)
    assert row == {}


def test_is_moving_threshold():
    assert _is_moving({"change_5m": 12.0}) is True
    assert _is_moving({"change_1h": -10.0}) is True
    assert _is_moving({"change_5m": 9.9, "change_1h": -3.0}) is False
    assert _is_moving({}) is False
