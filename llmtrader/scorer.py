"""Continuous market-context dimensions.

The reference architecture's central idea: instead of handing the model raw indicator
tables and making it synthesize a view from scratch every tick, publish continuous scores
that always have something to say. Every dimension here is computed the same way whether
the market is quiet or violent, so the model spends its capacity deciding rather than
recomputing.
"""

from dataclasses import dataclass, field

from .context.indicators import atr, bollinger, iqr_filter, percentile_rank

TREND_LABELS = (
    (60, "STRONG_BULL"),
    (20, "BULL"),
    (-20, "NEUTRAL"),
    (-60, "BEAR"),
)
MOMENTUM_LABELS = (
    (50, "STRONG_UP"),
    (15, "UP"),
    (-15, "FLAT"),
    (-50, "DOWN"),
)
VOLUME_LABELS = ((0.3, "DEAD"), (0.7, "BELOW_AVG"), (1.3, "NORMAL"), (2.0, "ABOVE_AVG"))
VOLUME_TOP = "SURGE"
VOLATILITY_LABELS = ((25, "LOW"), (75, "NORMAL"), (90, "HIGH"))
VOLATILITY_TOP = "EXTREME"
MR_SNAP_FLOOR = 15.0


def _clip(v, lo=-1.0, hi=1.0):
    if v is None:
        return None
    return max(lo, min(hi, v))


def _floor_label(value, table, top):
    for threshold, name in table:
        if value >= threshold:
            return name
    return top


def _ceil_label(value, bands, top):
    for threshold, name in bands:
        if value < threshold:
            return name
    return top


def _round(v, n=1):
    return None if v is None else round(v, n)


def _mean(parts):
    parts = [p for p in parts if p is not None]
    if not parts:
        return 0.0
    return sum(parts) / len(parts)


def _weighted(pairs):
    clean = [(v, w) for v, w in pairs if v is not None]
    total = sum(w for _, w in clean)
    if not total:
        return 0.0
    return sum(v * w for v, w in clean) / total


@dataclass
class ScoredContext:
    trend: float = 0.0
    trend_label: str = "NEUTRAL"
    trend_note: str = ""
    momentum: float = 0.0
    momentum_label: str = "FLAT"
    momentum_note: str = ""
    mr_pressure: float = 0.0
    mr_snap: str = "FLAT"
    mr_note: str = ""
    volatility: str = "NORMAL"
    volatility_note: str = ""
    volume_ratio: float = 1.0
    volume_label: str = "NORMAL"
    sr_level: str = ""
    sr_price: float = None
    sr_distance_atr: float = None
    key_levels: list = field(default_factory=list)
    gex: dict = None

    def headline(self):
        bits = [
            f"Trend {self.trend_label} ({self.trend:+.0f})",
            f"Momentum {self.momentum_label} ({self.momentum:+.0f})",
            f"MR {self.mr_pressure:.0f}/100 snap {self.mr_snap}",
            f"Vol {self.volatility}",
            f"Volume {self.volume_ratio:.2f}x {self.volume_label}",
        ]
        return " | ".join(bits)


def _trend(ctx):
    pairs = []
    vwap_up = ctx.session.above_vwap if ctx.session else True
    for tf, w in (("5m", 1.6), ("1h", 1.2), ("1m", 0.7)):
        c = ctx.timeframes.get(tf)
        if c is None:
            continue
        stack = {"bull": 1.0, "bear": -1.0}.get(c.ema_stack, 0.0)
        pairs.append((stack, w))
        if c.plus_di is not None and c.minus_di is not None:
            denom = (c.plus_di + c.minus_di) or 1.0
            di = (c.plus_di - c.minus_di) / denom
            strength = min((c.adx or 0.0) / 40.0, 1.0)
            pairs.append((di * strength, w))
        if c.slope_pct is not None:
            pairs.append((_clip(c.slope_pct / 0.08) * (c.trend_r2 or 0.0), w * 0.8))
        if c.rsi is not None:
            pairs.append(((c.rsi - 50.0) / 50.0, w * 0.6))
        if c.ema50 is not None:
            pairs.append((1.0 if c.last > c.ema50 else -1.0, w * 0.7))
    if ctx.session is not None:
        pairs.append((1.0 if vwap_up else -1.0, 1.4))
    score = 100.0 * _weighted([(v, w) for v, w in pairs if v is not None])
    return max(-100.0, min(100.0, score))


def _momentum(ctx, frames):
    pairs = []
    for tf, w in (("5m", 1.5), ("1m", 1.0), ("1h", 0.8)):
        c = ctx.timeframes.get(tf)
        if c is None:
            continue
        if c.roc is not None and c.atr_pct:
            pairs.append((_clip(c.roc / (c.atr_pct * 3.0)), w))
        if c.macd_hist is not None and c.atr:
            pairs.append((_clip(c.macd_hist / (c.atr * 0.25)), w))
        bars = frames.get(tf) if frames else None
        if bars and len(bars) >= 11:
            closes = [b.close for b in bars]
            recent = closes[-1] - closes[-6]
            prior = closes[-6] - closes[-11]
            scale = (closes[-1] * (c.atr_pct or 0.1) / 100.0) or 1.0
            pairs.append((_clip((recent - prior) / (scale * 3.0)), w * 0.9))
    score = 100.0 * _weighted([(v, w) for v, w in pairs if v is not None])
    return max(-100.0, min(100.0, score))


def _vwap_sigma(ctx, frames):
    bars = frames.get("1m") if frames else None
    if not bars or ctx.session is None or ctx.session.vwap is None:
        return None
    rth = [b for b in bars if b.et.date() == bars[-1].et.date()]
    if len(rth) < 10:
        return None
    typicals = [b.typical for b in rth]
    mean = sum(typicals) / len(typicals)
    var = sum((t - mean) ** 2 for t in typicals) / len(typicals)
    sd = var ** 0.5
    if sd == 0:
        return None
    return (ctx.price - ctx.session.vwap) / sd


def _mean_reversion(ctx, frames):
    """Pressure is how stretched price is (0-100). The snap direction is taken from whichever
    stretch measure dominates, so the direction can never contradict the reason for the score."""
    components = []

    def add(magnitude, snap, note):
        if magnitude is None:
            return
        magnitude = _clip(magnitude, 0.0, 1.0)
        if magnitude > 0:
            components.append((magnitude, snap, note))

    c1 = ctx.timeframes.get("1m")
    c5 = ctx.timeframes.get("5m")
    if c1 and c1.zscore is not None:
        add(abs(c1.zscore) / 3.0, "UP" if c1.zscore < 0 else "DOWN",
            f"1m z-score {c1.zscore:+.2f} vs its 20-bar mean")
    sigma = _vwap_sigma(ctx, frames)
    if sigma is not None:
        add(abs(sigma) / 3.0, "UP" if sigma < 0 else "DOWN",
            f"VWAP {sigma:+.1f} sigma")
    if c1 and c1.bb_pctb is not None:
        add(abs(c1.bb_pctb - 0.5) * 2.0, "UP" if c1.bb_pctb < 0.5 else "DOWN",
            f"bollinger position {c1.bb_pctb:.2f}")
    if c5 and c5.zscore is not None:
        add(abs(c5.zscore) / 3.0, "UP" if c5.zscore < 0 else "DOWN",
            f"5m z-score {c5.zscore:+.2f}")
    if c5 and c5.rsi is not None:
        add(abs(c5.rsi - 50.0) / 35.0, "UP" if c5.rsi < 50 else "DOWN",
            f"5m RSI {c5.rsi:.0f}")

    if not components:
        drift = 0.0
        if ctx.session is not None and ctx.session.vwap:
            drift = (ctx.price - ctx.session.vwap) / ctx.session.vwap
        snap = "FLAT" if abs(drift) < 0.0005 else ("DOWN" if drift > 0 else "UP")
        return 0.0, snap, ""

    components.sort(key=lambda c: -c[0])
    pressure = components[0][0] * 100.0
    snap = components[0][1] if pressure >= MR_SNAP_FLOOR else "FLAT"
    notes = [n for magnitude, _, n in components[:2] if magnitude * 100.0 >= 35.0]
    if not notes:
        notes = [components[0][2]]
    return pressure, snap, ", ".join(notes)


def _volatility(frames, ctx):
    bars = frames.get("5m") if frames else None
    if bars and len(bars) > 140:
        bars = bars[-140:]
    c5 = ctx.timeframes.get("5m")
    atr_pct = c5.atr_pct if c5 else None
    rank = None
    note = ""
    if bars and len(bars) >= 30:
        history = []
        for i in range(20, len(bars)):
            window = bars[: i + 1]
            a = atr([b.high for b in window], [b.low for b in window],
                    [b.close for b in window], 14)
            if a:
                history.append(a / window[-1].close * 100.0)
        if atr_pct is not None and history:
            rank = percentile_rank(history, atr_pct)
        widths = [
            bollinger([b.close for b in bars[: i + 1]], 20, 2.0)[4]
            for i in range(20, len(bars))
        ]
        widths = [w for w in widths if w is not None]
        if widths and c5 is not None and c5.bb_width_pct is not None:
            avg = sum(widths) / len(widths)
            if avg:
                ratio = c5.bb_width_pct / avg
                if ratio >= 1.15:
                    note = f"bands expanding ({ratio:.2f}x their 20-bar average)"
                elif ratio <= 0.85:
                    note = f"bands compressing ({ratio:.2f}x their 20-bar average)"
                else:
                    note = "bands steady"
    label = "NORMAL" if rank is None else _ceil_label(rank, VOLATILITY_LABELS, VOLATILITY_TOP)
    return label, rank, note


def _volume(ctx, frames):
    ratio = None
    c5 = ctx.timeframes.get("5m")
    if c5 and c5.rel_vol is not None:
        ratio = c5.rel_vol
    history = []
    bars = frames.get("1m") if frames else None
    if bars and ctx.session is not None:
        from .data.base import minutes_from_open

        elapsed = max(1, ctx.session.minutes_from_open)
        by_day = {}
        for b in bars:
            by_day.setdefault(b.et.date(), []).append(b)
        for day, day_bars in by_day.items():
            if day >= bars[-1].et.date():
                continue
            cum = sum(
                b.volume for b in day_bars if 0 <= minutes_from_open(b.ts) < elapsed
            )
            if cum > 0:
                history.append(cum)
    kept = iqr_filter(history)
    if kept and ctx.session and ctx.session.cum_volume:
        expected = sum(kept) / len(kept)
        if expected:
            ratio = ctx.session.cum_volume / expected
    ratio = ratio if ratio is not None else 1.0
    return ratio, _ceil_label(ratio, VOLUME_LABELS, VOLUME_TOP)


STRUCTURAL_LEVELS = (
    "prev_day_close",
    "prev_day_high",
    "prev_day_low",
    "session_open",
    "session_vwap",
    "premarket_high",
    "premarket_low",
    "pivot_PP",
    "pivot_R1",
    "pivot_S1",
    "pivot_R2",
    "pivot_S2",
)


def _sr(ctx):
    """Nearest structural level, in ATR units. Round numbers are excluded: they sit within
    half an ATR of price almost always, which would make the measure say nothing."""
    atr = None
    c5 = ctx.timeframes.get("5m")
    if c5 and c5.atr:
        atr = c5.atr
    best = (None, None, None)
    for name, level in ctx.key_levels or []:
        if name not in STRUCTURAL_LEVELS or level is None or level <= 0:
            continue
        distance = abs(ctx.price - level)
        if best[2] is None or distance < best[2]:
            best = (name, level, distance)
    name, level, distance = best
    if name is None:
        return "", None, None
    return name, level, (distance / atr if atr else None)


def score_context(ctx, frames=None):
    frames = frames or {}
    trend = _trend(ctx)
    momentum = _momentum(ctx, frames)
    pressure, snap, mr_note = _mean_reversion(ctx, frames)
    vol_label, vol_rank, vol_note = _volatility(frames, ctx)
    ratio, vol_ratio_label = _volume(ctx, frames)
    sr_name, sr_price, sr_dist = _sr(ctx)

    trend_note = ""
    c5 = ctx.timeframes.get("5m") or ctx.timeframes.get("1m")
    if c5 is not None:
        bits = []
        if c5.adx is not None:
            bits.append(f"adx {c5.adx:.0f}")
        if c5.ema_stack:
            bits.append(f"ema stack {c5.ema_stack}")
        trend_note = (c5.tf + ": " + ", ".join(bits)) if bits else ""
    momentum_note = ""
    if c5 is not None:
        bits = []
        if c5.roc is not None:
            bits.append(f"roc {c5.roc:+.2f}%")
        if c5.macd_hist is not None:
            bits.append(f"macd hist {c5.macd_hist:+.4f}")
        momentum_note = (c5.tf + ": " + ", ".join(bits)) if bits else ""

    return ScoredContext(
        trend=_round(trend, 1),
        trend_label=_floor_label(trend, TREND_LABELS, "STRONG_BEAR"),
        trend_note=trend_note,
        momentum=_round(momentum, 1),
        momentum_label=_floor_label(momentum, MOMENTUM_LABELS, "STRONG_DOWN"),
        momentum_note=momentum_note,
        mr_pressure=_round(pressure, 0),
        mr_snap=snap,
        mr_note=mr_note,
        volatility=vol_label,
        volatility_note=(f"{vol_note}, {vol_rank:.0f}th percentile" if vol_rank is not None
                         else vol_note),
        volume_ratio=_round(ratio, 2),
        volume_label=vol_ratio_label,
        sr_level=sr_name,
        sr_price=_round(sr_price, 2),
        sr_distance_atr=_round(sr_dist, 2),
        key_levels=list(ctx.key_levels or []),
    )
