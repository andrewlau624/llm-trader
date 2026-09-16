
import pytest

from llmtrader.context import indicators as ind


def test_sma_and_stdev():
    assert ind.sma([1, 2, 3, 4], 2) == 3.5
    assert ind.sma([1, 2], 5) is None
    assert ind.stdev([2, 4, 4, 4, 5, 5, 7, 9], 8) == pytest.approx(2.0)


def test_ema_matches_manual_seed():
    values = [float(i) for i in range(1, 11)]
    out = ind.ema_series(values, 5)
    k = 2 / 6
    seed = sum(values[:5]) / 5
    expected = seed
    for v in values[5:]:
        expected = expected + k * (v - expected)
    assert out[-1] == pytest.approx(expected)
    assert len(out) == len(values) - 5 + 1


def test_rsi_all_gains_is_100_and_all_losses_is_0():
    rising = [float(i) for i in range(1, 30)]
    falling = [float(i) for i in range(30, 1, -1)]
    assert ind.rsi(rising, 14) == 100.0
    assert ind.rsi(falling, 14) == pytest.approx(0.0, abs=1e-9)
    mixed = [10, 11, 10.5, 11.5, 11.2, 12, 11.8, 12.5, 12.2, 13, 12.8, 13.5, 13.2, 14, 13.8, 14.5]
    assert 0 < ind.rsi(mixed, 14) < 100


def test_atr_positive_and_wilder():
    highs = [10 + i * 0.5 for i in range(30)]
    lows = [9 + i * 0.5 for i in range(30)]
    closes = [9.5 + i * 0.5 for i in range(30)]
    a = ind.atr(highs, lows, closes, 14)
    assert a == pytest.approx(1.0, abs=1e-6)


def test_bollinger_bounds_and_pctb():
    closes = [10.0] * 19 + [20.0]
    mid, upper, lower, pctb, width = ind.bollinger(closes, 20, 2.0)
    assert lower <= mid <= upper
    assert pctb > 0.5
    assert width > 0


def test_macd_zero_on_constant_series():
    closes = [50.0] * 60
    macd_v, _signal, hist = ind.macd(closes)
    assert macd_v == pytest.approx(0.0)
    assert hist == pytest.approx(0.0)


def test_directional_adx_on_uptrend():
    closes = [100 + i for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    adx, pdi, mdi = ind.directional(highs, lows, closes, 14)
    assert pdi > mdi
    assert adx > 50


def test_linreg_slope_and_r2():
    slope, r2 = ind.linreg([1, 2, 3, 4, 5])
    assert slope == pytest.approx(1.0)
    assert r2 == pytest.approx(1.0)
    slope, r2 = ind.linreg([1, -1, 1, -1, 1])
    assert r2 < 0.3


def test_zscore_and_roc_and_vwap():
    closes = [10.0] * 20 + [12.0]
    assert ind.zscore(closes, 20) > 2
    assert ind.roc([10, 11], 1) == pytest.approx(10.0)
    from datetime import datetime, timezone

    from llmtrader.data.base import Bar

    b1 = Bar(datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc), 10, 10, 10, 10, 100)
    b2 = Bar(datetime(2024, 1, 2, 14, 31, tzinfo=timezone.utc), 20, 20, 20, 20, 300)
    assert ind.vwap([b1, b2]) == pytest.approx((10 * 100 + 20 * 300) / 400)


def test_resample_aggregates_ohlcv():
    from datetime import datetime, timezone

    from llmtrader.data.base import Bar, resample

    bars = [
        Bar(datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc), 10, 11, 9, 10.5, 100),
        Bar(datetime(2024, 1, 2, 14, 31, tzinfo=timezone.utc), 10.5, 12, 10, 11.5, 200),
        Bar(datetime(2024, 1, 2, 14, 35, tzinfo=timezone.utc), 11.5, 12.5, 11, 12, 300),
    ]
    out = resample(bars, 5, anchor=(9, 30))
    assert len(out) == 2
    assert out[0].open == 10
    assert out[0].high == 12
    assert out[0].low == 9
    assert out[0].close == 11.5
    assert out[0].volume == 300
    assert out[1].volume == 300
