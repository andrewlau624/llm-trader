from datetime import datetime, timedelta, timezone

from llmtrader.broker.accounting import LocalAccount
from llmtrader.broker.sim import SimBroker
from llmtrader.data.base import Bar
from llmtrader.trader import Decision


def bar(ts_min, o, h, lo, c, v=1000):
    return Bar(
        ts=datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=ts_min),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=v,
        symbol="TEST",
    )


def long_decision(entry=100.0, stop=99.0, tp=103.0, hold=60):
    return Decision(
        action="enter_long",
        confidence=8,
        entry=entry,
        stop_loss=stop,
        take_profit=tp,
        size_multiplier=1.0,
        max_hold_minutes=hold,
        thesis="t",
        invalidation="i",
    )


def test_entry_fills_at_next_bar_open(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    d = long_decision()
    broker.submit(d, 10, datetime(2026, 9, 16, 13, 30, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.5, 100.2))
    assert broker.account.position is None
    broker.process_bar(bar(1, 100.5, 101, 100, 100.8))
    pos = broker.account.position
    assert pos is not None
    assert pos.entry == 100.5
    assert pos.qty == 10
    assert broker.account.trades_today == 1


def test_stop_hit_produces_minus_one_r(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    broker.process_bar(bar(1, 100.2, 100.4, 99.5, 99.8))
    assert broker.account.position is not None
    broker.process_bar(bar(2, 99.8, 99.9, 98.5, 99.0))
    assert broker.account.position is None
    trade = broker.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit == 99.0
    assert trade.pnl == -10.0
    assert trade.r_multiple == -1.0
    assert trade.hold_min == 2


def test_take_profit_hit_produces_plus_three_r(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    broker.process_bar(bar(1, 100.2, 103.5, 100.1, 103.2))
    trade = broker.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.pnl == 30.0
    assert trade.r_multiple == 3.0


def test_stop_wins_when_bar_covers_both(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    broker.process_bar(bar(1, 100.2, 104.0, 98.0, 101.0))
    assert broker.trades[0].exit_reason == "stop"


def test_fill_bar_is_not_managed(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 90.0, 100.2))
    assert broker.account.position is not None
    assert broker.trades == []


def test_max_hold_exit_at_close(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=90.0, tp=200.0, hold=5), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    for i in range(7):
        broker.process_bar(bar(i, 100, 100.2, 99.8, 100.1))
    trade = broker.trades[0]
    assert trade.exit_reason == "max_hold"
    assert trade.hold_min == 5
    assert trade.exit == 100.1


def test_short_trade_direction_and_slippage(cfg):
    cfg.slippage_bps = 10.0
    broker = SimBroker(cfg, account=LocalAccount(100000))
    d = Decision(action="enter_short", confidence=8, entry=100.0, stop_loss=101.0,
                 take_profit=97.0, size_multiplier=1.0, max_hold_minutes=60,
                 thesis="t", invalidation="i")
    broker.submit(d, 10, datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.0))
    pos = broker.account.position
    assert pos.side == "short"
    assert pos.entry < 100.0
    broker.process_bar(bar(1, 100, 100.1, 96.5, 97.0))
    trade = broker.trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.pnl > 0


def test_commission_is_charged(cfg):
    cfg.commission_per_share = 0.01
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    broker.process_bar(bar(1, 100.2, 103.5, 100.1, 103.2))
    assert broker.trades[0].pnl == 30.0 - 0.2


def test_account_marks_unrealized_and_tracks_streaks(cfg):
    acct = LocalAccount(100000)
    broker = SimBroker(cfg, account=acct)
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    broker.process_bar(bar(1, 100.2, 100.4, 99.5, 99.8))
    assert acct.mark(102.0) == 100000 + 20.0
    broker.process_bar(bar(2, 99.8, 99.9, 98.5, 99.0))
    assert acct.realized_pnl == -10.0
    assert acct.equity == 99990.0
    assert acct.consecutive_losses == 1
    assert acct.losses == 1


def test_force_close_records_trade(cfg):
    broker = SimBroker(cfg, account=LocalAccount(100000))
    broker.submit(long_decision(stop=90.0, tp=200.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    trade = broker.force_close(101.0, datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc),
                               reason="replay_end")
    assert trade.exit_reason == "replay_end"
    assert trade.pnl == 10.0
    assert broker.account.position is None


def test_day_pnl_is_balance_delta_not_trade_sum(cfg):
    """A trade sum can be wrong; the account balance cannot. day_pnl must follow the balance."""
    acct = LocalAccount(100000)
    acct.start_day("2026-09-16", 100000)
    broker = SimBroker(cfg, account=acct)
    broker.submit(long_decision(stop=99.0, tp=103.0), 10,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    assert acct.day_pnl == 0.0
    assert acct.mark(101.0) == 100010.0
    assert acct.day_pnl == 10.0
    broker.process_bar(bar(1, 100.2, 103.5, 100.1, 103.2))
    assert acct.day_pnl == 30.0
    assert acct.realized_pnl == 30.0


def test_daily_halt_follows_the_balance_even_with_no_closed_trades(cfg):
    """Unrealized drawdown on an open position must still move the daily loss halt."""
    from llmtrader.risk import update_halt

    acct = LocalAccount(100000)
    acct.start_day("2026-09-16", 100000)
    broker = SimBroker(cfg, account=acct)
    broker.submit(long_decision(stop=50.0, tp=200.0), 100,
                  datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc))
    broker.process_bar(bar(0, 100, 100.5, 99.9, 100.2))
    assert not acct.halted
    acct.mark(90.0)
    update_halt(acct, cfg)
    assert acct.halted
    assert "daily loss limit" in acct.halt_reason


def test_authoritative_balance_is_not_overwritten_by_marks(cfg):
    acct = LocalAccount(100000, authoritative_equity=True)
    acct.start_day("2026-09-16", 100000)
    acct.set_balance(100250.0)
    assert acct.day_pnl == 250.0
    acct.mark(50.0)
    assert acct.equity == 100250.0
    assert acct.day_pnl == 250.0
