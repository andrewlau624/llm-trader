import math


def sma(values, n):
    if len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / n


def stdev(values, n):
    if len(values) < n:
        return None
    window = values[-n:]
    mean = sum(window) / n
    var = sum((v - mean) ** 2 for v in window) / n
    return math.sqrt(var)


def ema_series(values, n):
    if len(values) < n:
        return []
    k = 2.0 / (n + 1)
    seed = sum(values[:n]) / n
    out = [seed]
    for v in values[n:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def ema(values, n):
    s = ema_series(values, n)
    return s[-1] if s else None


def wilder_smooth(values, n):
    if len(values) < n:
        return None
    acc = sum(values[:n]) / n
    for v in values[n:]:
        acc = (acc * (n - 1) + v) / n
    return acc


def true_ranges(highs, lows, closes):
    out = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        out.append(tr)
    return out


def atr(highs, lows, closes, n=14):
    trs = true_ranges(highs, lows, closes)
    if len(trs) < n:
        return None
    return wilder_smooth(trs, n)


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(d, 0.0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0.0)) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    fast_s = ema_series(closes, fast)
    slow_s = ema_series(closes, slow)
    offset = len(fast_s) - len(slow_s)
    macd_line = [f - s for f, s in zip(fast_s[offset:], slow_s, strict=True)]
    macd_val = macd_line[-1]
    signal_s = ema_series(macd_line, signal)
    if not signal_s:
        return macd_val, None, None
    signal_val = signal_s[-1]
    return macd_val, signal_val, macd_val - signal_val


def bollinger(closes, n=20, k=2.0):
    mid = sma(closes, n)
    sd = stdev(closes, n)
    if mid is None or sd is None:
        return None, None, None, None, None
    upper = mid + k * sd
    lower = mid - k * sd
    last = closes[-1]
    pct_b = 0.5 if upper == lower else (last - lower) / (upper - lower)
    width_pct = 0.0 if mid == 0 else (upper - lower) / mid * 100.0
    return mid, upper, lower, pct_b, width_pct


def stochastic(highs, lows, closes, n=14, d=3):
    if len(closes) < n + d:
        return None, None
    ks = []
    for i in range(len(closes) - d, len(closes)):
        hh = max(highs[i - n + 1 : i + 1])
        ll = min(lows[i - n + 1 : i + 1])
        ks.append(50.0 if hh == ll else (closes[i] - ll) / (hh - ll) * 100.0)
    return ks[-1], sum(ks) / len(ks)


def wilder_series(values, n):
    if len(values) < n:
        return []
    acc = sum(values[:n]) / n
    out = [acc]
    for v in values[n:]:
        acc = (acc * (n - 1) + v) / n
        out.append(acc)
    return out


def directional(highs, lows, closes, n=14):
    if len(closes) < 2 * n + 1:
        return None, None, None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        )
    sm_tr = wilder_series(trs, n)
    sm_plus = wilder_series(plus_dm, n)
    sm_minus = wilder_series(minus_dm, n)
    if not sm_tr:
        return None, None, None
    dx_series = []
    for tr_v, p, m in zip(sm_tr, sm_plus, sm_minus, strict=True):
        if tr_v == 0:
            continue
        plus_di = 100.0 * p / tr_v
        minus_di = 100.0 * m / tr_v
        denom = plus_di + minus_di
        dx_series.append(0.0 if denom == 0 else 100.0 * abs(plus_di - minus_di) / denom)
    if len(dx_series) < n:
        return None, plus_di, minus_di
    adx_series = wilder_series(dx_series, n)
    if not adx_series:
        return None, plus_di, minus_di
    return adx_series[-1], plus_di, minus_di


def roc(closes, n=5):
    if len(closes) < n + 1 or closes[-n - 1] == 0:
        return None
    return (closes[-1] - closes[-n - 1]) / closes[-n - 1] * 100.0


def zscore(closes, n=20):
    m = sma(closes, n)
    sd = stdev(closes, n)
    if m is None or sd is None or sd == 0:
        return None
    return (closes[-1] - m) / sd


def linreg(values):
    n = len(values)
    if n < 3:
        return None, None
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    sxx = sum((i - x_mean) ** 2 for i in range(n))
    sxy = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    if sxx == 0:
        return None, None
    slope = sxy / sxx
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    ss_res = sum((v - (y_mean + slope * (i - x_mean))) ** 2 for i, v in enumerate(values))
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r2


def vwap(bars):
    num = sum(b.typical * b.volume for b in bars)
    den = sum(b.volume for b in bars)
    if den == 0:
        return None
    return num / den


def relative_volume(bars, lookback=20):
    if len(bars) < 2:
        return None
    window = bars[-lookback - 1 : -1]
    if not window:
        return None
    avg = sum(b.volume for b in window) / len(window)
    if avg == 0:
        return None
    return bars[-1].volume / avg


def swing_structure(bars, lookback=6):
    if len(bars) < lookback * 2 + 1:
        return "unknown"
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    recent_h = max(highs[-lookback:])
    prev_h = max(highs[-2 * lookback : -lookback])
    recent_l = min(lows[-lookback:])
    prev_l = min(lows[-2 * lookback : -lookback])
    if recent_h > prev_h and recent_l > prev_l:
        return "hh_hl"
    if recent_h < prev_h and recent_l < prev_l:
        return "lh_ll"
    return "range"


def percentile_rank(values, value):
    if not values:
        return None
    below = sum(1 for v in values if v <= value)
    return below / len(values) * 100.0
