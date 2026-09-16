import pytest
from helpers import make_account, make_ctx

from llmtrader import risk
from llmtrader.trader import Decision


def entry(action="enter_long", entry=100.0, stop=99.0, tp=102.0, confidence=8, mult=1.0):
    return Decision(
        action=action,
        confidence=confidence,
        entry=entry,
        stop_loss=stop,
        take_profit=tp,
        size_multiplier=mult,
        max_hold_minutes=60,
        thesis="t",
        invalidation="i",
    )


def test_hold_is_never_an_entry(cfg):
    ctx = make_ctx()
    check = risk.check(Decision(action="hold"), ctx, make_account(), cfg)
    assert not check.allowed
    assert check.reasons == ["hold"]


def test_valid_entry_is_allowed_and_sized_by_risk(cfg):
    cfg.max_notional_pct = 100.0
    ctx = make_ctx(price=100.0, atr=1.0)
    check = risk.check(entry(stop=99.0, tp=102.0), ctx, make_account(100000), cfg)
    assert check.allowed, check.reasons
    assert check.risk_dollars == pytest.approx(250.0)
    assert check.size == pytest.approx(250.0)


def test_notional_cap_limits_size(cfg):
    cfg.max_notional_pct = 1.0
    ctx = make_ctx(price=100.0, atr=1.0)
    check = risk.check(entry(stop=99.0, tp=102.0), ctx, make_account(100000), cfg)
    assert check.allowed
    assert check.size == pytest.approx(10.0)


def test_low_confidence_rejected(cfg):
    ctx = make_ctx()
    check = risk.check(entry(confidence=5), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("confidence" in r for r in check.reasons)


def test_poor_reward_risk_rejected(cfg):
    ctx = make_ctx(price=100.0, atr=1.0)
    check = risk.check(entry(stop=99.5, tp=100.5), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("reward:risk" in r for r in check.reasons)


def test_wrong_side_stop_rejected(cfg):
    ctx = make_ctx(price=100.0, atr=1.0)
    check = risk.check(entry(stop=101.0, tp=103.0), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("below entry" in r for r in check.reasons)


def test_entry_too_far_from_price_rejected(cfg):
    ctx = make_ctx(price=100.0, atr=1.0)
    check = risk.check(entry(entry=101.0, stop=100.5, tp=102.0), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("from price" in r for r in check.reasons)


def test_stop_tighter_than_half_atr_rejected(cfg):
    ctx = make_ctx(price=100.0, atr=2.0)
    check = risk.check(entry(stop=99.5, tp=103.0), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("tighter than 0.5x ATR" in r for r in check.reasons)


def test_stop_wider_than_three_atr_rejected(cfg):
    ctx = make_ctx(price=100.0, atr=0.5)
    check = risk.check(entry(stop=97.0, tp=110.0), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("wider than 3x ATR" in r for r in check.reasons)


def test_missing_stop_rejected(cfg):
    ctx = make_ctx()
    d = entry()
    d.stop_loss = None
    check = risk.check(d, ctx, make_account(), cfg)
    assert not check.allowed
    assert any("missing entry or stop" in r for r in check.reasons)


def test_max_trades_per_day_rejected(cfg):
    ctx = make_ctx()
    check = risk.check(entry(), ctx, make_account(trades_today=cfg.max_trades_per_day), cfg)
    assert not check.allowed
    assert any("max trades per day" in r for r in check.reasons)


def test_position_open_blocks_new_entry(cfg):
    from datetime import datetime, timezone

    from llmtrader.broker.base import Position

    ctx = make_ctx(price=100.0)
    pos = Position(symbol="TEST", side="long", qty=10, entry=99.0, stop=98.0,
                   take_profit=102.0, opened_at=datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc))
    check = risk.check(entry(), ctx, make_account(position=pos), cfg)
    assert not check.allowed
    assert "already in a position" in check.reasons


def test_halted_account_blocks_entry(cfg):
    ctx = make_ctx()
    check = risk.check(entry(), ctx, make_account(halted=True, halt_reason="daily loss"), cfg)
    assert not check.allowed
    assert any("halted" in r for r in check.reasons)


def test_consecutive_losses_require_high_confidence(cfg):
    ctx = make_ctx(price=100.0, atr=1.0)
    acct = make_account(consecutive_losses=cfg.max_consecutive_losses)
    check = risk.check(entry(confidence=7), ctx, acct, cfg)
    assert not check.allowed
    assert any("consecutive losses" in r for r in check.reasons)
    check = risk.check(entry(confidence=8), ctx, acct, cfg)
    assert check.allowed, check.reasons


def test_shorts_can_be_disabled(cfg):
    cfg.allow_shorts = False
    ctx = make_ctx(price=100.0)
    check = risk.check(entry(action="enter_short", stop=101.0, tp=97.0), ctx, make_account(), cfg)
    assert not check.allowed
    assert "shorts disabled" in check.reasons


def test_no_entry_in_first_and_last_minutes(cfg):
    ctx = make_ctx(price=100.0, minute_open=2, minute_close=200)
    check = risk.check(entry(), ctx, make_account(), cfg, day="d")
    assert not check.allowed
    assert any("too early" in r for r in check.reasons)
    ctx = make_ctx(price=100.0, minute_open=60, minute_close=3)
    check = risk.check(entry(), ctx, make_account(), cfg, day="d")
    assert not check.allowed
    assert any("too close to close" in r for r in check.reasons)


def test_unparseable_decision_rejected(cfg):
    ctx = make_ctx()
    d = entry()
    d.parse_ok = False
    d.parse_error = "not json"
    check = risk.check(d, ctx, make_account(), cfg)
    assert not check.allowed
    assert "could not be parsed" in check.reasons[0]


def test_update_halt_on_daily_loss_limit(cfg):
    from llmtrader.broker.accounting import LocalAccount

    acct = LocalAccount(100000)
    acct.start_day("2026-09-16", 100000)
    acct.day_pnl = -500
    risk.update_halt(acct, cfg)
    assert not acct.halted
    acct.day_pnl = -1001
    risk.update_halt(acct, cfg)
    assert acct.halted
    assert "daily loss limit" in acct.halt_reason


def test_position_size_scales_with_multiplier(cfg):
    cfg.max_notional_pct = 100.0
    acct = make_account(100000)
    size, risk_dollars = risk.position_size(cfg, acct, 100.0, 99.0, multiplier=2.0)
    assert risk_dollars == pytest.approx(500.0)
    assert size == pytest.approx(500.0)


def test_counter_trend_entry_needs_high_confidence(cfg):
    cfg.require_regime_alignment = True
    ctx = make_ctx(price=100.0, atr=1.0, regime="trend_down")
    check = risk.check(entry(confidence=8), ctx, make_account(), cfg)
    assert not check.allowed
    assert any("fights the trend_down regime" in r for r in check.reasons)
    check = risk.check(entry(confidence=9), ctx, make_account(), cfg)
    assert check.allowed, check.reasons
    ctx = make_ctx(price=100.0, atr=1.0, regime="trend_up")
    check = risk.check(entry(action="enter_short", stop=101.0, tp=97.0, confidence=8), ctx,
                       make_account(), cfg)
    assert not check.allowed
    check = risk.check(entry(action="enter_short", stop=101.0, tp=97.0, confidence=9), ctx,
                       make_account(), cfg)
    assert check.allowed, check.reasons


def test_with_trend_entries_are_not_blocked(cfg):
    ctx = make_ctx(price=100.0, atr=1.0, regime="trend_up")
    check = risk.check(entry(confidence=6), ctx, make_account(), cfg)
    assert check.allowed, check.reasons


def test_regime_alignment_can_be_disabled(cfg):
    cfg.require_regime_alignment = False
    ctx = make_ctx(price=100.0, atr=1.0, regime="trend_down")
    check = risk.check(entry(confidence=8), ctx, make_account(), cfg)
    assert check.allowed, check.reasons
