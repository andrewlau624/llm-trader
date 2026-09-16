from datetime import date

from llmtrader.feedback import trade_feedback

DAY = date(2026, 9, 16)


def trade(side, pnl, r, reason, regime, hour=10, minute=0):
    stamp = f"2026-09-16T{hour:02d}:{minute:02d}:00+00:00"
    return {
        "closed_at": stamp,
        "side": side,
        "pnl": pnl,
        "r_multiple": r,
        "exit_reason": reason,
        "hold_min": 12,
        "regime": regime,
    }


def test_no_trades_means_no_feedback():
    assert trade_feedback([], day=DAY) == []


def test_other_days_are_ignored():
    old = trade("short", -50.0, -1.0, "stop", "trend_up")
    old["closed_at"] = "2026-09-15T10:00:00+00:00"
    assert trade_feedback([old], day=DAY) == []


def test_summary_line_counts_wins_and_reports_the_balance_change():
    trades = [
        trade("long", 40.0, 1.6, "take_profit", "trend_up"),
        trade("short", -25.0, -1.0, "stop", "trend_up"),
    ]
    lines = trade_feedback(trades, day=DAY)
    assert any("TODAY: 2 closed (1W/1L)" in line for line in lines)
    assert any("net +15.00" in line for line in lines)


def test_repeated_failure_is_called_out_by_side_and_regime():
    trades = [
        trade("short", -40.0, -1.0, "stop", "trend_up"),
        trade("short", -52.0, -1.05, "stop", "trend_up"),
        trade("short", -8.0, -0.2, "max_hold", "trend_up"),
    ]
    lines = trade_feedback(trades, day=DAY)
    assert any("REPEATED FAILURE" in line and "3 SHORT entries" in line for line in lines)
    assert any("0 winners" in line for line in lines)


def test_directional_bias_flags_trading_against_the_trend():
    trades = [
        trade("short", -40.0, -1.0, "stop", "trend_up"),
        trade("short", -20.0, -0.9, "stop", "trend_up"),
    ]
    lines = trade_feedback(trades, day=DAY)
    assert any("DIRECTIONAL BIAS" in line for line in lines)
    assert any("against the prevailing trend" in line for line in lines)


def test_losing_streak_is_reported_but_broken_by_a_win():
    losers = [
        trade("long", -10.0, -1.0, "stop", "mixed", hour=9),
        trade("long", -10.0, -1.0, "stop", "mixed", hour=10),
        trade("long", -10.0, -1.0, "stop", "mixed", hour=11),
    ]
    assert any("LOSING STREAK: the last 3" in line for line in trade_feedback(losers, day=DAY))
    recovery = [*losers, trade("long", 30.0, 2.0, "take_profit", "mixed", hour=12)]
    assert not any("LOSING STREAK" in line for line in trade_feedback(recovery, day=DAY))


def test_time_stops_dominating_is_surfaced():
    trades = [
        trade("long", -1.0, -0.1, "max_hold", "mixed", hour=9),
        trade("long", -2.0, -0.2, "max_hold", "mixed", hour=10),
    ]
    lines = trade_feedback(trades, day=DAY)
    assert any("TIME STOPS DOMINATE" in line for line in lines)


def test_exit_mix_lists_reasons_in_a_stable_order():
    trades = [
        trade("long", 10.0, 1.0, "take_profit", "trend_up", hour=9),
        trade("long", -10.0, -1.0, "stop", "trend_up", hour=10),
        trade("long", -10.0, -1.0, "stop", "trend_up", hour=11),
    ]
    line = next(x for x in trade_feedback(trades, day=DAY) if x.startswith("EXITS:"))
    assert line.index("stop") < line.index("take_profit")


def test_feedback_is_bounded_in_length():
    trades = [
        trade("short", -10.0, -1.0, "stop", "trend_up", hour=9 + i % 12) for i in range(20)
    ]
    assert len(trade_feedback(trades, day=DAY)) <= 6
