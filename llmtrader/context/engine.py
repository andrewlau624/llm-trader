from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from ..data.base import (
    ET,
    TIMEFRAME_MINUTES,
    minutes_from_open,
    minutes_to_close,
    resample,
    session_phase,
    to_utc,
)
from . import indicators as ind


def _round(v, n=2):
    return None if v is None else round(v, n)


def _completed(bars, minutes, now):
    return [b for b in bars if b.ts + timedelta(minutes=minutes) <= now]


@dataclass
class TFContext:
    tf: str
    bars: int
    last: float
    net_change_pct: float
    ema9: float
    ema21: float
    ema50: float
    ema_stack: str
    slope_pct: float
    trend_r2: float
    adx: float
    plus_di: float
    minus_di: float
    rsi: float
    stoch_k: float
    stoch_d: float
    macd: float
    macd_signal: float
    macd_hist: float
    roc: float
    bb_pctb: float
    bb_width_pct: float
    atr: float
    atr_pct: float
    zscore: float
    structure: str
    rel_vol: float


def _ema_stack(ema9, ema21, ema50):
    if None in (ema9, ema21, ema50):
        return "unknown"
    if ema9 > ema21 > ema50:
        return "bull"
    if ema9 < ema21 < ema50:
        return "bear"
    return "mixed"


def tf_context(tf, bars):
    if len(bars) < 5:
        return None
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    last = closes[-1]
    ema9 = ind.ema(closes, 9)
    ema21 = ind.ema(closes, 21)
    ema50 = ind.ema(closes, 50)
    slope, r2 = ind.linreg(closes[-15:])
    adx, pdi, mdi = ind.directional(highs, lows, closes, 14)
    macd_v, macd_s, macd_h = ind.macd(closes)
    _, _, _, pctb, width = ind.bollinger(closes, 20, 2.0)
    atr_v = ind.atr(highs, lows, closes, 14)
    sk, sd = ind.stochastic(highs, lows, closes)
    first = closes[0] or None
    return TFContext(
        tf=tf,
        bars=len(bars),
        last=_round(last),
        net_change_pct=_round((last - first) / first * 100.0) if first else None,
        ema9=_round(ema9),
        ema21=_round(ema21),
        ema50=_round(ema50),
        ema_stack=_ema_stack(ema9, ema21, ema50),
        slope_pct=_round(slope / last * 100.0, 4) if slope is not None and last else None,
        trend_r2=_round(r2, 3),
        adx=_round(adx, 1),
        plus_di=_round(pdi, 1),
        minus_di=_round(mdi, 1),
        rsi=_round(ind.rsi(closes), 1),
        stoch_k=_round(sk, 1),
        stoch_d=_round(sd, 1),
        macd=_round(macd_v, 4),
        macd_signal=_round(macd_s, 4),
        macd_hist=_round(macd_h, 4),
        roc=_round(ind.roc(closes, 5), 3),
        bb_pctb=_round(pctb, 3),
        bb_width_pct=_round(width, 3),
        atr=_round(atr_v, 3),
        atr_pct=_round(atr_v / last * 100.0, 3) if atr_v and last else None,
        zscore=_round(ind.zscore(closes, 20), 2),
        structure=ind.swing_structure(bars),
        rel_vol=_round(ind.relative_volume(bars), 2),
    )


@dataclass
class SessionContext:
    session_open: float
    session_high: float
    session_low: float
    session_range: float
    range_pos_pct: float
    gap_pct: float
    prev_close: float
    prev_high: float
    prev_low: float
    prev_session_change_pct: float
    vwap: float
    vwap_dist_pct: float
    above_vwap: bool
    premarket_high: float
    premarket_low: float
    minutes_from_open: int
    minutes_to_close: int
    phase: str
    cum_volume: float
    expected_cum_volume: float
    rvol: float


def _agg(bars):
    if not bars:
        return None
    return {
        "open": bars[0].open,
        "high": max(b.high for b in bars),
        "low": min(b.low for b in bars),
        "close": bars[-1].close,
        "volume": sum(b.volume for b in bars),
    }


def session_context(bars_1m, now):
    now = to_utc(now)
    day = now.astimezone(ET).date()
    by_day = {}
    for b in bars_1m:
        by_day.setdefault(b.et.date(), []).append(b)
    days = sorted(by_day)
    if not days:
        return None
    today = [b for b in by_day.get(day, []) if minutes_from_open(b.ts) >= 0]
    pre = [b for b in by_day.get(day, []) if minutes_from_open(b.ts) < 0]
    if not today:
        return None
    agg = _agg(today)
    prev_days = [d for d in days if d < day]
    prev = _agg(by_day[prev_days[-1]]) if prev_days else None
    vw = ind.vwap(today)
    price = today[-1].close
    rng = agg["high"] - agg["low"]
    prev_close = prev["close"] if prev else agg["open"]
    elapsed = max(1, minutes_from_open(now))
    history = []
    for d in prev_days[-5:]:
        cum = sum(b.volume for b in by_day[d] if 0 <= minutes_from_open(b.ts) < elapsed)
        if cum > 0:
            history.append(cum)
    expected = sum(history) / len(history) if history else agg["volume"]
    return SessionContext(
        session_open=_round(agg["open"]),
        session_high=_round(agg["high"]),
        session_low=_round(agg["low"]),
        session_range=_round(rng),
        range_pos_pct=_round((price - agg["low"]) / rng * 100.0, 1) if rng else 50.0,
        gap_pct=_round((agg["open"] - prev_close) / prev_close * 100.0, 3) if prev_close else 0.0,
        prev_close=_round(prev_close),
        prev_high=_round(prev["high"]) if prev else None,
        prev_low=_round(prev["low"]) if prev else None,
        prev_session_change_pct=_round((prev["close"] - prev["open"]) / prev["open"] * 100.0, 3)
        if prev
        else None,
        vwap=_round(vw),
        vwap_dist_pct=_round((price - vw) / vw * 100.0, 3) if vw else 0.0,
        above_vwap=bool(vw and price >= vw),
        premarket_high=_round(max((b.high for b in pre), default=agg["open"])),
        premarket_low=_round(min((b.low for b in pre), default=agg["open"])),
        minutes_from_open=minutes_from_open(now),
        minutes_to_close=minutes_to_close(now),
        phase=session_phase(now),
        cum_volume=_round(agg["volume"], 0),
        expected_cum_volume=_round(expected, 0),
        rvol=_round(agg["volume"] / expected, 2) if expected else None,
    )


@dataclass
class MarketContext:
    symbol: str
    now: datetime
    price: float
    timeframes: dict
    session: SessionContext
    regime: str
    regime_notes: list
    key_levels: list
    cross_market: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def classify_regime(tfs, session):
    notes = []
    r5 = tfs.get("5m")
    r1 = tfs.get("1m")
    ref = r5 or r1
    if ref is None:
        return "unknown", notes
    adx = ref.adx or 0.0
    stack = ref.ema_stack
    hist = ref.macd_hist or 0.0
    atr_pct = ref.atr_pct or 0.0
    width = ref.bb_width_pct or 0.0
    trending = adx >= 22
    if trending and stack == "bull" and hist > 0:
        regime = "trend_up"
        notes.append(f"5m adx {adx:.0f} with bullish ema stack and positive macd histogram")
    elif trending and stack == "bear" and hist < 0:
        regime = "trend_down"
        notes.append(f"5m adx {adx:.0f} with bearish ema stack and negative macd histogram")
    elif width <= 0.25 and adx < 18:
        regime = "quiet_range"
        notes.append(f"narrow 5m bollinger width {width:.2f}% and adx {adx:.0f}")
    elif adx < 18:
        regime = "choppy_range"
        notes.append(f"5m adx {adx:.0f} below trend threshold")
    else:
        regime = "mixed"
        notes.append(f"5m adx {adx:.0f} but ema stack {stack} conflicts with histogram {hist:+.4f}")
    if atr_pct >= 0.15:
        notes.append(f"elevated 5m atr {atr_pct:.2f}% of price")
    if session:
        if session.range_pos_pct is not None:
            if session.range_pos_pct > 85:
                notes.append("price pinned near session high")
            elif session.range_pos_pct < 15:
                notes.append("price pinned near session low")
        if session.rvol and session.rvol >= 1.5:
            notes.append(f"session volume {session.rvol:.1f}x normal for this time of day")
        elif session.rvol and session.rvol <= 0.6:
            notes.append(f"session volume only {session.rvol:.1f}x normal for this time of day")
    return regime, notes


def key_levels(session, price):
    levels = []
    if session:
        levels += [
            ("prev_day_close", session.prev_close),
            ("prev_day_high", session.prev_high),
            ("prev_day_low", session.prev_low),
            ("session_open", session.session_open),
            ("session_vwap", session.vwap),
            ("premarket_high", session.premarket_high),
            ("premarket_low", session.premarket_low),
        ]
    for step in (1, 5):
        levels.append((f"round_{step}", round(price / step) * step))
    seen, out = set(), []
    for name, lvl in levels:
        if lvl is None:
            continue
        key = (name, round(lvl, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append((name, lvl))
    return out


def build_context(symbol, bars_1m, now, timeframes=("1m", "5m", "1h"), prebuilt=None, extra=None):
    if not bars_1m:
        raise ValueError("no bars supplied")
    now = to_utc(now)
    prebuilt = prebuilt or {}
    frames = {}
    for tf in timeframes:
        minutes = TIMEFRAME_MINUTES[tf]
        if prebuilt.get(tf):
            frames[tf] = _completed(prebuilt[tf], minutes, now)
        elif tf == "1m":
            frames[tf] = _completed(bars_1m, 1, now)
        else:
            src = _completed(bars_1m, 1, now)
            anchor = (9, 30) if minutes in (5, 15) else None
            frames[tf] = _completed(resample(src, minutes, anchor=anchor), minutes, now)
    tfs = {}
    for tf in timeframes:
        c = tf_context(tf, frames.get(tf, []))
        if c is not None:
            tfs[tf] = c
    base = frames.get("1m") or _completed(bars_1m, 1, now) or bars_1m
    sess = session_context(base, now)
    price = base[-1].close if base else bars_1m[-1].close
    regime, notes = classify_regime(tfs, sess)
    return MarketContext(
        symbol=symbol,
        now=now,
        price=_round(price),
        timeframes=tfs,
        session=sess,
        regime=regime,
        regime_notes=notes,
        key_levels=key_levels(sess, price),
        cross_market=extra or {},
    )
