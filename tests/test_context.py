import pytest
from helpers import make_bars

from llmtrader.context import build_context
from llmtrader.data.base import Bar


def test_partial_bar_is_excluded():
    bars = make_bars(30)
    ctx = build_context("TEST", bars, bars[-1].ts)
    assert ctx.timeframes["1m"].bars == 29
    assert ctx.timeframes["5m"].bars == 5


def test_derived_5m_buckets_are_complete_only():
    bars = make_bars(35)
    ctx = build_context("TEST", bars, bars[-1].ts, timeframes=("1m", "5m"))
    assert ctx.timeframes["5m"].bars == 6
    assert ctx.timeframes["1m"].bars == 34


def test_prebuilt_higher_timeframe_is_used():
    from datetime import timedelta


    start = make_bars(1)[0].ts - timedelta(days=4)
    hourly = []
    price = 100.0
    for i in range(60):
        price += 0.1
        hourly.append(
            Bar(ts=start + timedelta(hours=i), open=price - 0.1, high=price + 0.2,
                low=price - 0.2, close=price, volume=5000, symbol="TEST")
        )
    min_bars_1m = make_bars(120)
    ctx = build_context(
        "TEST", min_bars_1m, min_bars_1m[-1].ts, timeframes=("1m", "5m", "1h"),
        prebuilt={"1h": hourly},
    )
    assert ctx.timeframes["1h"].ema_stack == "bull"
    assert ctx.timeframes["1h"].rsi > 90


def test_session_gap_and_range_position():
    prev = make_bars(10, day=(2026, 9, 15), start_price=100.0, step=0.0)
    today = make_bars(10, day=(2026, 9, 16), start_price=102.0, step=0.1)
    bars = prev + today
    ctx = build_context("TEST", bars, bars[-1].ts)
    assert ctx.session.gap_pct == pytest.approx(2.0, abs=0.01)
    assert ctx.session.range_pos_pct > 90
    assert ctx.session.prev_close == pytest.approx(100.0)
    assert ctx.session.above_vwap


def test_regime_trend_up_on_clean_uptrend():
    bars = make_bars(400, start_price=100.0, step=0.1, accel=0.0008)
    ctx = build_context("TEST", bars, bars[-1].ts)
    assert ctx.regime == "trend_up"
    assert ctx.timeframes["5m"].ema_stack == "bull"
    assert any("adx" in n for n in ctx.regime_notes)


def test_regime_choppy_on_flat_price():
    bars = make_bars(400, start_price=100.0, step=0.0)
    ctx = build_context("TEST", bars, bars[-1].ts)
    assert ctx.regime in ("choppy_range", "quiet_range")
    assert ctx.timeframes["5m"].ema_stack == "mixed"


def test_key_levels_include_session_and_prev_day():
    prev = make_bars(10, day=(2026, 9, 15), start_price=100.0, step=0.0)
    today = make_bars(62, day=(2026, 9, 16), start_price=101.0, step=0.01)
    bars = prev + today
    ctx = build_context("TEST", bars, bars[-1].ts)
    names = [n for n, _ in ctx.key_levels]
    assert "prev_day_close" in names
    assert "session_vwap" in names
    assert "session_open" in names
    assert "prev_day_high" in names


def test_unusual_volume_shows_in_notes():
    prev = make_bars(60, day=(2026, 9, 15), start_price=100.0, step=0.0, volume=1000)
    today = make_bars(60, day=(2026, 9, 16), start_price=100.0, step=0.0, volume=1000)
    today = [
        Bar(ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
            volume=b.volume * 4, symbol=b.symbol)
        for b in today
    ]
    ctx = build_context("TEST", prev + today, today[-1].ts)
    assert ctx.session.rvol > 1.5
    assert any("volume" in n for n in ctx.regime_notes)


def test_context_serialises_to_dict():
    bars = make_bars(120)
    ctx = build_context("TEST", bars, bars[-1].ts)
    d = ctx.to_dict()
    assert d["symbol"] == "TEST"
    assert "timeframes" in d and "1m" in d["timeframes"]
