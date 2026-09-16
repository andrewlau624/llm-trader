from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
PRE_OPEN = dtime(4, 0)


@dataclass
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str = ""

    def __post_init__(self):
        if self.ts.tzinfo is None:
            self.ts = self.ts.replace(tzinfo=UTC)

    @property
    def et(self):
        return self.ts.astimezone(ET)

    @property
    def typical(self):
        return (self.high + self.low + self.close) / 3.0


def to_utc(ts):
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def session_date(bars):
    return bars[-1].et.date() if bars else None


def in_rth(ts):
    t = to_utc(ts).astimezone(ET).time()
    return RTH_OPEN <= t < RTH_CLOSE


def is_trading_day(ts):
    return to_utc(ts).astimezone(ET).weekday() < 5


def rth_bars(bars):
    return [b for b in bars if in_rth(b.ts)]


def today_bars(bars, day=None):
    if not bars:
        return []
    day = day or bars[-1].et.date()
    return [b for b in bars if b.et.date() == day]


def minutes_to_close(ts):
    et = to_utc(ts).astimezone(ET)
    close = et.replace(hour=RTH_CLOSE.hour, minute=RTH_CLOSE.minute, second=0, microsecond=0)
    return int((close - et).total_seconds() // 60)


def minutes_from_open(ts):
    et = to_utc(ts).astimezone(ET)
    op = et.replace(hour=RTH_OPEN.hour, minute=RTH_OPEN.minute, second=0, microsecond=0)
    return int((et - op).total_seconds() // 60)


def session_phase(ts):
    m = minutes_from_open(ts)
    left = minutes_to_close(ts)
    if m < 0:
        return "premarket"
    if m < 30:
        return "open_drive"
    if m < 120:
        return "morning"
    if left > 120:
        return "midday_lull"
    if left > 30:
        return "afternoon"
    return "power_hour"


def bucket_start(ts, minutes, anchor=None):
    et = to_utc(ts).astimezone(ET)
    base = et.replace(second=0, microsecond=0)
    if anchor is None:
        floor_min = (base.minute // minutes) * minutes
        return base.replace(minute=floor_min).astimezone(UTC)
    a = et.replace(hour=anchor[0], minute=anchor[1], second=0, microsecond=0)
    delta = int((et - a).total_seconds() // (minutes * 60))
    return (a + timedelta(minutes=delta * minutes)).astimezone(UTC)


def resample(bars, minutes, anchor=None):
    buckets = {}
    order = []
    for b in bars:
        key = bucket_start(b.ts, minutes, anchor)
        if key not in buckets:
            buckets[key] = {"o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": 0.0}
            order.append(key)
        agg = buckets[key]
        agg["h"] = max(agg["h"], b.high)
        agg["l"] = min(agg["l"], b.low)
        agg["c"] = b.close
        agg["v"] += b.volume
    return [
        Bar(ts=k, open=buckets[k]["o"], high=buckets[k]["h"], low=buckets[k]["l"],
            close=buckets[k]["c"], volume=buckets[k]["v"], symbol=bars[0].symbol)
        for k in order
    ]


TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def build_timeframes(bars_1m, timeframes=("1m", "5m", "1h")):
    out = {"1m": list(bars_1m)}
    for tf in timeframes:
        if tf == "1m":
            continue
        minutes = TIMEFRAME_MINUTES[tf]
        anchor = (9, 30) if minutes in (5, 15) else None
        out[tf] = resample(bars_1m, minutes, anchor=anchor)
    return out
