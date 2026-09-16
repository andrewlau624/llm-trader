from datetime import datetime, timedelta, timezone

from llmtrader.data.base import Bar
from llmtrader.runner import cross_market, recent_lines, to_utc_stamp


class FakeFeed:
    def __init__(self, bars_by_symbol=None, raises=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.raises = raises or set()
        self.calls = []

    def bars(self, symbol, tf="5m", limit=200, cache_age_s=45):
        self.calls.append((symbol, tf, limit, cache_age_s))
        if symbol in self.raises:
            raise RuntimeError("boom")
        bars = self.bars_by_symbol.get(symbol, [])
        return bars[-limit:]


def series(start_price, count, minutes=5, start=None):
    start = start or datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)
    out = []
    price = start_price
    for i in range(count):
        price = price * (1 + 0.001)
        out.append(
            Bar(ts=start + timedelta(minutes=i * minutes), open=price, high=price + 0.1,
                low=price - 0.1, close=price, volume=1000, symbol="X")
        )
    return out


def test_cross_market_returns_change_percent(cfg):
    cfg.extra_context_symbols = ["AAA"]
    feed = FakeFeed({"AAA": series(100.0, 60)})
    now = datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)
    out = cross_market(feed, cfg, now)
    assert list(out) == ["AAA 5m"]
    assert out["AAA 5m"] > 0


def test_cross_market_warns_when_data_missing(cfg):
    cfg.extra_context_symbols = ["AAA", "BBB"]
    feed = FakeFeed({"AAA": series(100.0, 60), "BBB": []})
    warnings = []
    out = cross_market(feed, cfg, datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
                       warnings=warnings)
    assert "AAA 5m" in out
    assert any("BBB data incomplete" in w for w in warnings)


def test_cross_market_warns_on_exception(cfg):
    cfg.extra_context_symbols = ["CCC"]
    feed = FakeFeed(raises={"CCC"})
    warnings = []
    cross_market(feed, cfg, datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
                 warnings=warnings)
    assert any("CCC data unavailable" in w for w in warnings)


def test_cross_market_flags_zero_close(cfg):
    cfg.extra_context_symbols = ["DDD"]
    bars = series(100.0, 30)
    for b in bars:
        b.close = 0.0
    feed = FakeFeed({"DDD": bars})
    warnings = []
    cross_market(feed, cfg, datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc),
                 warnings=warnings)
    assert warnings


def test_cross_market_uses_longer_cache_than_the_main_feed(cfg):
    cfg.extra_context_symbols = ["AAA"]
    feed = FakeFeed({"AAA": series(100.0, 60)})
    cross_market(feed, cfg, datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc))
    assert feed.calls[0][3] >= 300


def test_recent_lines_formats_decisions_and_trades():
    decisions = [
        {"ts": "2026-09-16T13:35:00+00:00",
         "decision": {"action": "enter_short", "confidence": 7, "entry": 100.0,
                      "stop_loss": 101.0, "take_profit": 98.0},
         "risk": {"allowed": False, "reasons": ["reward:risk 1.20 < minimum 1.5"]}},
        {"ts": "2026-09-16T13:40:00+00:00",
         "decision": {"action": "hold", "confidence": 5}, "risk": {"allowed": False,
                                                                    "reasons": ["hold"]}},
    ]
    trades = [
        {"closed_at": "2026-09-16T14:00:00+00:00", "side": "short", "exit_reason": "stop",
         "pnl": -25.0, "r_multiple": -1.0, "hold_min": 20}
    ]
    lines = recent_lines(decisions, trades)
    assert any("REJECTED" in x and "reward:risk" in x for x in lines)
    assert any("CLOSED short stop" in x and "-1.00R" in x for x in lines)


def test_recent_lines_marks_parse_failure():
    decisions = [
        {"ts": "2026-09-16T13:35:00+00:00",
         "decision": {"action": "hold", "confidence": 1, "parse_ok": False},
         "risk": {"allowed": False, "reasons": ["hold"]}}
    ]
    assert any("PARSE FAILURE" in x for x in recent_lines(decisions, []))


def test_to_utc_stamp_handles_strings_and_objects():
    assert to_utc_stamp(None) is None
    assert to_utc_stamp("not-a-date") is None
    assert to_utc_stamp("2026-09-16T13:35:00+00:00").hour == 13
    assert to_utc_stamp(datetime(2026, 9, 16, 13, 35, tzinfo=timezone.utc)).minute == 35
