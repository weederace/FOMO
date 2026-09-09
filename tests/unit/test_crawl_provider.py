from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.crawl import (
    CrawlProvider,
    CrawlProviderError,
    alert_event_id,
    build_alert,
    build_thesis,
    build_trader,
    parse_amount,
    parse_count,
    parse_handle,
    parse_rank,
)

CAPTURED_AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

# Shape captured from a real rendered row on the public page.
TOP_ROW = {
    "rank": "01",
    "handle": "@Proteus",
    "displayName": "Proteus",
    "pnl": "+$1,187,274",
    "volume": "$1.2M",
    "trades": "42",
    "followers": "3.4K",
    "holdings": "5",
    "avatar": "https://prod-fomo-profile-pics.s3.amazonaws.com/da45_small.jpg",
    "solanaWallet": "Goj6MczfEqArgzWMxLFAi5Z1MbPPMb1PjWcCjg5gSBBx",
    "evmWallet": "0x77b61ec8bf2b36a115b9e1d2ba7d5061e4938da0",
}

UNRESOLVED_WALLET_ROW = {
    "rank": "07",
    "handle": "@stigstigstig_",
    "displayName": "Still in the Game",
    "pnl": "+$558,905",
    "volume": "$15.2M",
    "trades": "1,865",
    "followers": "24.1K",
    "holdings": "10",
    "avatar": None,
    "solanaWallet": None,
    "evmWallet": None,
}

THESIS_ROW = {
    "handle": "@workethic",
    "token": "$BONER",
    "text": "sellers feeling limp",
    "holding": "$994.8K",
    "unrealised": "$935.3K",
    "realised": "-$403",
}


def test_amounts_read_compact_and_exact_rendered_values() -> None:
    assert parse_amount("+$1,187,274") == Decimal("1187274")
    assert parse_amount("$1.2M") == Decimal("1200000")
    assert parse_amount("$829K") == Decimal("829000")
    assert parse_amount("3.4K") == Decimal("3400")
    assert parse_amount("3,255") == Decimal("3255")
    assert parse_amount("-$500") == Decimal("-500")


def test_amounts_reject_placeholders_and_prose() -> None:
    assert parse_amount(None) is None
    assert parse_amount("") is None
    assert parse_amount("resolving…") is None
    assert parse_amount("Loading live data from /v2/leaderboard/24h…") is None
    assert parse_count("-5") is None


def test_rank_and_handle_are_read_from_display_text() -> None:
    assert parse_rank("01") == 1
    assert parse_rank("10") == 10
    assert parse_rank("—") is None
    assert parse_rank("0") is None
    assert parse_handle("@Proteus") == "Proteus"
    assert parse_handle("  ") is None


def test_trader_row_is_normalized_with_the_full_wallet() -> None:
    trader = build_trader(TOP_ROW, CAPTURED_AT)
    assert trader is not None
    assert trader.platform == "fomo-crawl"
    assert trader.platform_trader_id == "Proteus"
    assert trader.username == "Proteus"
    assert trader.display_name == "Proteus"
    assert trader.profile_url == "https://fomo.family/profile/Proteus"
    # The visible chip is truncated; the full address comes from the copy attribute.
    assert trader.wallet_address == "0x77b61ec8bf2b36a115b9e1d2ba7d5061e4938da0"
    assert trader.rank == 1
    assert trader.pnl == Decimal("1187274")
    assert trader.volume == Decimal("1200000")
    assert trader.total_trades == 42
    assert trader.follower_count == 3400
    assert trader.captured_at == CAPTURED_AT


def test_trader_row_survives_unresolved_wallets() -> None:
    trader = build_trader(UNRESOLVED_WALLET_ROW, CAPTURED_AT)
    assert trader is not None
    assert trader.wallet_address is None
    assert trader.display_name == "Still in the Game"
    assert trader.total_trades == 1865


def test_rows_without_a_handle_are_dropped() -> None:
    assert build_trader({**TOP_ROW, "handle": None}, CAPTURED_AT) is None
    assert build_trader({}, CAPTURED_AT) is None


def test_alert_rows_are_parsed_into_dedupable_payloads() -> None:
    alert = build_alert({"tag": "buy", "text": "samwangio bought $NUDES ($7K size)", "age": "1m"}, CAPTURED_AT)
    assert alert is not None
    assert alert["type"] == "buy"
    assert alert["trader"] == "samwangio"
    assert alert["token"] == "NUDES"
    assert alert["side"] == "bought"
    assert alert["usdValue"] == 7000.0
    assert alert["id"] == alert_event_id("buy", "samwangio bought $NUDES ($7K size)")


def test_realized_alerts_and_free_form_alerts_are_both_kept() -> None:
    sold = build_alert({"tag": "sell", "text": "kongkong sold $BEN (+$8K realized)", "age": "now"}, CAPTURED_AT)
    assert sold is not None
    assert sold["side"] == "sold"
    assert sold["usdValue"] == 8000.0
    perp = build_alert({"tag": "alert", "text": "rylancom Open Short 40x $BTC perp", "age": "1m"}, CAPTURED_AT)
    assert perp is not None
    assert perp["type"] == "alert"
    assert "trader" not in perp
    assert build_alert({"tag": "buy", "text": "   "}, CAPTURED_AT) is None


def test_theses_keep_the_reasoning_and_the_position_behind_it() -> None:
    thesis = build_thesis(THESIS_ROW, CAPTURED_AT)
    assert thesis is not None
    assert thesis["handle"] == "workethic"
    assert thesis["token"] == "BONER"
    assert thesis["text"] == "sellers feeling limp"
    assert thesis["holdingUsd"] == 994800.0
    assert thesis["unrealizedPnlUsd"] == 935300.0
    assert thesis["realizedPnlUsd"] == -403.0


def test_theses_without_a_handle_or_body_are_dropped() -> None:
    assert build_thesis({**THESIS_ROW, "text": "  "}, CAPTURED_AT) is None
    assert build_thesis({**THESIS_ROW, "handle": None}, CAPTURED_AT) is None
    bare = build_thesis({"handle": "@x", "text": "no numbers shown"}, CAPTURED_AT)
    assert bare is not None
    assert bare["token"] is None
    assert "holdingUsd" not in bare


def test_capabilities_report_only_what_a_public_page_shows() -> None:
    capabilities = CrawlProvider().capabilities()
    assert capabilities["leaderboard"] is True
    assert capabilities["alerts"] is True
    assert capabilities["user_profile"] is False
    assert capabilities["trades"] is False
    assert capabilities["balances"] is False


def test_unsupported_windows_are_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="window must be one of"):
        CrawlProvider(window="12h")


async def test_leaderboard_normalizes_and_applies_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CrawlProvider(limit=1)
    rendered = {"rows": {"24h": [TOP_ROW, UNRESOLVED_WALLET_ROW]}, "alerts": [], "captured_at": CAPTURED_AT}
    monkeypatch.setattr(provider, "_cached_render", lambda: _resolved(rendered))
    traders = await provider.get_leaderboard()
    assert [item.username for item in traders] == ["Proteus"]
    assert provider.request_metrics()["last_row_count"] == 2


async def test_empty_pages_fail_instead_of_returning_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = CrawlProvider()
    empty = {"rows": {"24h": []}, "alerts": [], "captured_at": CAPTURED_AT}
    monkeypatch.setattr(provider, "_cached_render", lambda: _resolved(empty))
    with pytest.raises(CrawlProviderError, match="no leaderboard rows"):
        await provider.get_leaderboard()


async def test_requesting_an_unrendered_window_is_explicit() -> None:
    with pytest.raises(CrawlProviderError, match="CRAWL_LEADERBOARD_WINDOW=7d"):
        await CrawlProvider(window="24h").get_leaderboard(window="7d")


async def test_alerts_are_capped_by_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"tag": "buy", "text": f"trader{index} bought $AAA ($1K size)", "age": "now"} for index in range(5)]
    provider = CrawlProvider(alert_limit=2)
    rendered = {"rows": {"24h": []}, "alerts": rows, "captured_at": CAPTURED_AT}
    monkeypatch.setattr(provider, "_cached_render", lambda: _resolved(rendered))
    assert len(await provider.get_alerts(50)) == 2


async def test_scan_rejects_a_window_the_page_does_not_have() -> None:
    with pytest.raises(CrawlProviderError, match="Unknown leaderboard window: 12h"):
        await CrawlProvider().scan(("24h", "12h"))


async def test_scan_collects_every_window_alerts_and_theses(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "rows": {"24h": [TOP_ROW], "7d": [UNRESOLVED_WALLET_ROW, TOP_ROW]},
        "alerts": [{"tag": "buy", "text": "samwangio bought $NUDES ($7K size)", "age": "1m"}],
        "theses": [THESIS_ROW],
        "captured_at": CAPTURED_AT,
    }
    provider = CrawlProvider()
    monkeypatch.setattr(provider, "_render", lambda *_args, **_kwargs: _resolved(payload))
    scan = await provider.scan(("24h", "7d"))
    assert list(scan.windows) == ["24h", "7d"]
    assert len(scan.alerts) == 1
    assert len(scan.theses) == 1
    assert sorted(scan.unique_traders()) == ["Proteus", "stigstigstig_"]
    assert scan.placements()["Proteus"] == [("24h", 1), ("7d", 1)]


async def _resolved(payload: dict) -> dict:
    return payload
