import math
import pickle
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import Bar, to_utc

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"


def _cache_path(symbol, interval, days):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{symbol}_{interval}_{days}d.pkl"


def _read_cache(path, max_age_s):
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > max_age_s:
        return None
    try:
        with path.open("rb") as fh:
            return pickle.load(fh)
    except Exception:
        return None


def _write_cache(path, bars):
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(bars, fh)
    tmp.replace(path)


def _frame_to_bars(df, symbol):
    bars = []
    if df is None or len(df) == 0:
        return bars
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel(1, axis=1)
    cols = {c.lower(): c for c in df.columns}
    for ts, row in df.iterrows():
        try:
            op = float(row[cols["open"]])
            hi = float(row[cols["high"]])
            lo = float(row[cols["low"]])
            cl = float(row[cols["close"]])
            vol = float(row[cols["volume"]])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isnan(op) or math.isnan(cl) or math.isnan(vol):
            continue
        bars.append(Bar(ts=to_utc(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts),
                        open=op, high=hi, low=lo, close=cl, volume=vol, symbol=symbol))
    bars.sort(key=lambda b: b.ts)
    return bars


class YFinanceFeed:
    name = "yfinance"

    def __init__(self, frozen=False):
        self.frozen = frozen
        self._mem = {}

    def bars_1m(self, symbol, days=7, cache_age_s=45):
        key = ("1m", symbol, days)
        if self.frozen and key in self._mem:
            return self._mem[key]
        path = _cache_path(symbol, "1m", days)
        if cache_age_s:
            cached = _read_cache(path, cache_age_s)
            if cached:
                if self.frozen:
                    self._mem[key] = cached
                return cached
        import yfinance as yf

        df = yf.download(
            symbol,
            period=f"{min(days, 7)}d",
            interval="1m",
            progress=False,
            auto_adjust=False,
            prepost=True,
            threads=False,
        )
        bars = _frame_to_bars(df, symbol)
        if bars:
            _write_cache(path, bars)
            if self.frozen:
                self._mem[key] = bars
        return bars

    def bars(self, symbol, tf="5m", limit=200, cache_age_s=45, period=None):
        key = (tf, symbol, limit, period)
        if self.frozen and key in self._mem:
            return self._mem[key]
        path = _cache_path(symbol, tf, f"{limit}_{period or 'default'}")
        if cache_age_s:
            cached = _read_cache(path, cache_age_s)
            if cached:
                if self.frozen:
                    self._mem[key] = cached
                return cached
        import yfinance as yf

        period = period or {1: "1d", 5: "5d", 15: "5d", 60: "1mo"}[
            {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[tf]
        ]
        df = yf.download(
            symbol, period=period, interval=tf, progress=False, auto_adjust=False, prepost=False
        )
        bars = _frame_to_bars(df, symbol)
        if period.endswith("d") and int(period[:-1]) > 7 and bars:
            bars = [b for b in bars if b.et.hour >= 9 and b.et.hour < 16]
        out = bars[-limit:] if bars else bars
        if bars:
            _write_cache(path, bars)
            if self.frozen:
                self._mem[key] = out
        return out


class AlpacaFeed:
    name = "alpaca"

    def __init__(self, api_key=None, secret_key=None, feed="iex", frozen=False):
        from alpaca.data.historical import StockHistoricalDataClient

        from ..config import alpaca_keys

        key, secret = alpaca_keys()
        self.client = StockHistoricalDataClient(api_key or key, secret_key or secret)
        self.feed = feed
        self.frozen = frozen
        self._mem = {}

    def _request(self, symbol, timeframe, start, end):
        from alpaca.data.requests import StockBarsRequest

        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=timeframe, start=start, end=end, feed=self.feed
        )
        data = self.client.get_stock_bars(req)
        try:
            raw = data.data.get(symbol, [])
        except AttributeError:
            raw = data.get(symbol, [])
        return [
            Bar(ts=to_utc(b.timestamp), open=float(b.open), high=float(b.high), low=float(b.low),
                close=float(b.close), volume=float(b.volume), symbol=symbol)
            for b in raw
        ]

    def bars_1m(self, symbol, days=3, cache_age_s=30):
        from alpaca.data.timeframe import TimeFrame

        path = _cache_path(symbol, "1m_alpaca", days)
        if cache_age_s:
            cached = _read_cache(path, cache_age_s)
            if cached:
                return cached
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        bars = self._request(symbol, TimeFrame.Minute, start, end)
        if bars:
            _write_cache(path, bars)
        return bars

    def bars(self, symbol, tf="5m", limit=200, cache_age_s=30):
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        mapping = {
            "1m": TimeFrame.Minute,
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "1h": TimeFrame.Hour,
        }
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=400 if tf == "1h" else 10)
        bars = self._request(symbol, mapping[tf], start, end)
        return bars[-limit:] if bars else bars


def get_feed(name="yfinance", frozen=False, **kwargs):
    if name == "alpaca":
        return AlpacaFeed(**kwargs)
    return YFinanceFeed(frozen=frozen)
