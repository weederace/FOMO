from app.analyzers.emerging import is_emerging
from app.analyzers.live_analytics import classify_trade_event, flow_summary, whale_score
from app.analyzers.metrics import Metric, rank_momentum, win_rate
from app.analyzers.whale_score import calculate


def test_win_rate_requires_sufficient_data():
    assert win_rate(2, 2).sufficient is False
    assert win_rate(8, 10).value == 0.8


def test_score_uses_available_weights_without_zero_imputation():
    result = calculate({"win_rate": Metric(80, 80, True, "ok")}, 50, 30)
    assert result.score == 80
    assert result.confidence < 100


def test_rank_momentum_rewards_lower_rank():
    assert rank_momentum(850, 140).value == 710


def test_emerging_requires_multiple_observations():
    assert is_emerging([850, 400, 140], [50, 65, 87], 1)
    assert not is_emerging([850, 140], [50, 87], 1)


def test_live_score_ignores_followers():
    base = {"pnl": 1000, "volume": 10000, "trades": 20, "followers": 0}
    popular = {**base, "followers": 1_000_000}
    assert whale_score(base, present_windows=4) == whale_score(popular, present_windows=4)


def test_trade_event_distinguishes_buy_sell_and_received():
    assert classify_trade_event("buy", "completed")["acquisition"] == "PLATFORM_BUY"
    assert classify_trade_event("sell", "completed")["event"] == "SELL"
    assert classify_trade_event("transfer_in", "confirmed")["event"] == "RECEIVED"


def test_flow_summary_exposes_token_events():
    result = flow_summary([{"token_symbol": "MOCK", "side": "buy", "status": "filled", "size_usd": 500}])
    assert result["events"][0]["acquisition"] == "PLATFORM_BUY"


def test_flow_summary_windows_and_event_ordering():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    old = (now - timedelta(hours=20)).isoformat()
    fresh = (now - timedelta(seconds=30)).isoformat()
    trades = [
        {"token_symbol": "OLD", "side": "buy", "status": "filled", "size_usd": 250, "executed_at": old},
        {"token_symbol": "NEW", "side": "sell", "status": "filled", "size_usd": 900, "executed_at": fresh},
    ]
    result = flow_summary(trades, now=now)

    # 24h default window keeps both events; newest first with real timestamps.
    assert result["recent_count"] == 2
    assert [event["token"] for event in result["events"]] == ["NEW", "OLD"]
    assert result["events"][0]["at"] is not None and "T" in result["events"][0]["at"]

    # A five-minute window drops the 20h-old event.
    tight = flow_summary(trades, now=now, recent_minutes=5)
    assert tight["recent_count"] == 1
    assert [event["token"] for event in tight["events"]] == ["NEW"]

    # The event list is capped so a busy day cannot flood the payload.
    flood = [{"token_symbol": f"T{i}", "side": "buy", "size_usd": 100,
              "executed_at": (now - timedelta(seconds=i)).isoformat()} for i in range(300)]
    assert len(flow_summary(flood, now=now)["events"]) == 200


def test_flow_summary_volume_changes_cover_all_tokens_and_windows():
    """token_volume_changes lists every traded token (even Δ-0 ones) and the
    5m/1h deltas use disjoint back-to-back windows."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    trades = [
        # AAA: 100 in the last 5m and 100 in the 5m before it → Δ5m = 0.
        {"token_symbol": "AAA", "side": "buy", "size_usd": 100,
         "executed_at": (now - timedelta(minutes=1)).isoformat()},
        {"token_symbol": "AAA", "side": "buy", "size_usd": 100,
         "executed_at": (now - timedelta(minutes=8)).isoformat()},
        # BBB: only an older trade inside 1h, outside both 5m windows.
        {"token_symbol": "BBB", "side": "buy", "size_usd": 250,
         "executed_at": (now - timedelta(minutes=30)).isoformat()},
        # DDD: volume in the previous 5m but none now → Δ5m negative.
        {"token_symbol": "DDD", "side": "buy", "size_usd": 400,
         "executed_at": (now - timedelta(minutes=7)).isoformat()},
        # CCC: only a 20h-old trade — still listed with zero volumes.
        {"token_symbol": "CCC", "side": "buy", "size_usd": 900,
         "executed_at": (now - timedelta(hours=20)).isoformat()},
    ]
    changes = {item["token"]: item
               for item in flow_summary(trades, now=now)["token_volume_changes"]}

    assert set(changes) == {"AAA", "BBB", "CCC", "DDD"}
    assert changes["AAA"]["volume_5m"] == 100
    assert changes["AAA"]["increase_5m"] == 0
    assert changes["AAA"]["volume_1h"] == 200
    assert changes["BBB"]["volume_1h"] == 250
    assert changes["BBB"]["increase_5m"] == 0
    assert changes["DDD"]["volume_5m"] == 0
    assert changes["DDD"]["increase_5m"] == -400
    assert changes["CCC"]["volume_1h"] == 0 and changes["CCC"]["increase_1h"] == 0
