import pytest
from helpers import make_bars, make_ctx

from llmtrader.scorer import (
    TREND_LABELS,
    VOLATILITY_LABELS,
    VOLATILITY_TOP,
    VOLUME_LABELS,
    VOLUME_TOP,
    ScoredContext,
    _ceil_label,
    _floor_label,
    _mean_reversion,
    _sr,
    iqr_filter,
    score_context,
)


class _Session:
    def __init__(self, vwap=100.0):
        self.vwap = vwap
        self.above_vwap = True
        self.cum_volume = 1000
        self.minutes_from_open = 60


class _TF:
    def __init__(self, z=None, rsi=None, bb=None):
        self.zscore = z
        self.rsi = rsi
        self.bb_pctb = bb
        self.atr = None
        self.atr_pct = None
        self.roc = None
        self.macd_hist = None
        self.adx = None


class _Ctx:
    def __init__(self, c1, c5, vwap=100.0, price=100.0):
        self.timeframes = {"1m": c1, "5m": c5}
        self.session = _Session(vwap)
        self.price = price
        self.key_levels = []


def test_trend_labels_map_both_directions():
    assert _floor_label(80, TREND_LABELS, "STRONG_BEAR") == "STRONG_BULL"
    assert _floor_label(30, TREND_LABELS, "STRONG_BEAR") == "BULL"
    assert _floor_label(0, TREND_LABELS, "STRONG_BEAR") == "NEUTRAL"
    assert _floor_label(-30, TREND_LABELS, "STRONG_BEAR") == "BEAR"
    assert _floor_label(-90, TREND_LABELS, "STRONG_BEAR") == "STRONG_BEAR"


def test_ascending_band_labels_do_not_collapse():
    assert _ceil_label(5, VOLATILITY_LABELS, VOLATILITY_TOP) == "LOW"
    assert _ceil_label(50, VOLATILITY_LABELS, VOLATILITY_TOP) == "NORMAL"
    assert _ceil_label(80, VOLATILITY_LABELS, VOLATILITY_TOP) == "HIGH"
    assert _ceil_label(97, VOLATILITY_LABELS, VOLATILITY_TOP) == "EXTREME"
    assert _ceil_label(0.2, VOLUME_LABELS, VOLUME_TOP) == "DEAD"
    assert _ceil_label(0.5, VOLUME_LABELS, VOLUME_TOP) == "BELOW_AVG"
    assert _ceil_label(1.0, VOLUME_LABELS, VOLUME_TOP) == "NORMAL"
    assert _ceil_label(1.8, VOLUME_LABELS, VOLUME_TOP) == "ABOVE_AVG"
    assert _ceil_label(3.0, VOLUME_LABELS, VOLUME_TOP) == "SURGE"


def test_iqr_filter_drops_a_restart_artifact():
    values = [100, 102, 98, 101, 99, 100, 103, 5000]
    kept = iqr_filter(values)
    assert 5000 not in kept
    assert len(kept) == 7
    assert iqr_filter([1, 2]) == [1, 2]


def test_trend_score_signs_and_bounds():
    bull = make_ctx(price=100.0, tfs=None, ema_stack="bull", adx=35.0, macd_hist=0.05,
                    slope_pct=0.05, trend_r2=0.8, rsi=62.0)
    bear = make_ctx(price=100.0, ema_stack="bear", adx=35.0, macd_hist=-0.05,
                    slope_pct=-0.05, trend_r2=0.8, rsi=38.0)
    bull.session.above_vwap = True
    bear.session.above_vwap = False
    s_bull = score_context(bull, {})
    s_bear = score_context(bear, {})
    assert s_bull.trend > 40
    assert s_bull.trend_label in ("BULL", "STRONG_BULL")
    assert s_bear.trend < -40
    assert s_bear.trend_label in ("BEAR", "STRONG_BEAR")
    assert -100 <= s_bull.trend <= 100


def test_momentum_uses_frame_acceleration():
    rising = make_bars(60, start_price=100.0, step=0.2)
    flat = make_bars(60, start_price=100.0, step=0.0)
    ctx_rising = make_ctx(price=100.0, tfs=None, roc=0.4, macd_hist=0.2, atr=1.0)
    ctx_flat = make_ctx(price=100.0, tfs=None, roc=0.0, macd_hist=0.0, atr=1.0)
    up = score_context(ctx_rising, {"5m": rising})
    down = score_context(ctx_flat, {"5m": flat})
    assert up.momentum > down.momentum
    assert up.momentum_label in ("UP", "STRONG_UP")


def test_mean_reversion_snap_follows_the_dominant_stretch():
    below = _Ctx(_TF(z=-2.86, bb=-0.21), _TF(z=-0.2, rsi=43.9))
    above = _Ctx(_TF(z=2.5, bb=1.3), _TF(z=0.4, rsi=72.0))
    flat = _Ctx(_TF(z=0.05, bb=0.5), _TF(z=0.1, rsi=51.0))
    p_below, snap_below, note = _mean_reversion(below, {})
    p_above, snap_above, _ = _mean_reversion(above, {})
    p_flat, snap_flat, _ = _mean_reversion(flat, {})
    assert p_below > 90 and snap_below == "UP"
    assert p_above > 90 and snap_above == "DOWN"
    assert p_flat < 15 and snap_flat == "FLAT"
    assert "z-score" in note


def test_scored_context_headline_reads_like_a_decision_input():
    ctx = make_ctx(price=100.0, zscore=-2.4)
    ctx.session.above_vwap = False
    scored = score_context(ctx, {"5m": make_bars(60)})
    assert isinstance(scored, ScoredContext)
    headline = scored.headline()
    for token in ("Trend", "Momentum", "MR", "Vol", "Volume"):
        assert token in headline
    assert scored.volume_ratio is not None


def test_sr_proximity_ignores_round_numbers():
    ctx = make_ctx(price=100.0)
    ctx.key_levels = [("round_1", 100.0), ("prev_day_high", 104.0), ("pivot_PP", 97.0)]
    name, price, distance = _sr(ctx)
    assert name == "pivot_PP"
    assert price == 97.0
    assert distance == pytest.approx(3.0)


def test_sr_proximity_returns_empty_without_structure():
    ctx = make_ctx(price=100.0)
    ctx.key_levels = [("round_1", 100.0)]
    assert _sr(ctx) == ("", None, None)


def test_volatility_and_volume_available_from_frames():
    ctx = make_ctx(price=100.0)
    bars = make_bars(120, start_price=100.0, step=0.05)
    scored = score_context(ctx, {"5m": bars, "1m": bars})
    assert scored.volatility in ("LOW", "NORMAL", "HIGH", "EXTREME")
    assert scored.volume_label in ("DEAD", "BELOW_AVG", "NORMAL", "ABOVE_AVG", "SURGE")
