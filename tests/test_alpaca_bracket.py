import pytest

from llmtrader.broker.alpaca import bracket_problems


def test_long_bracket_valid_when_stop_below_and_target_above_live_price():
    assert bracket_problems("enter_long", 99.0, 103.0, 100.0) == []


def test_long_bracket_rejects_stop_above_live_price():
    problems = bracket_problems("enter_long", 101.0, 103.0, 100.0)
    assert any("long stop" in p for p in problems)


def test_long_bracket_rejects_target_below_live_price():
    problems = bracket_problems("enter_long", 99.0, 99.5, 100.0)
    assert any("long target" in p for p in problems)


def test_long_bracket_rejects_stop_exactly_at_live_price():
    assert bracket_problems("enter_long", 100.0, 103.0, 100.0)


def test_short_bracket_valid_when_stop_above_and_target_below_live_price():
    assert bracket_problems("enter_short", 101.0, 97.0, 100.0) == []


def test_short_bracket_rejects_stop_below_live_price():
    problems = bracket_problems("enter_short", 99.0, 97.0, 100.0)
    assert any("short stop" in p for p in problems)


def test_short_bracket_rejects_target_above_live_price():
    problems = bracket_problems("enter_short", 101.0, 100.5, 100.0)
    assert any("short target" in p for p in problems)


def test_no_live_price_means_no_objection():
    assert bracket_problems("enter_long", 101.0, 99.0, None) == []


def test_stale_entry_stop_above_live_price_is_caught():
    problems = bracket_problems("enter_long", 758.5, 763.0, 753.96)
    assert len(problems) == 1
    assert "long stop" in problems[0]


def test_stale_entry_target_below_live_price_is_caught():
    problems = bracket_problems("enter_long", 752.0, 753.0, 753.96)
    assert len(problems) == 1
    assert "long target" in problems[0]


@pytest.mark.parametrize("base", [100.0, 753.96, 0.51])
def test_boundary_is_strict(base):
    assert bracket_problems("enter_long", base - 0.01, base + 0.01, base) == []
    assert bracket_problems("enter_long", base, base, base)


def test_alpaca_refuses_to_submit_outside_rth():
    from datetime import datetime, timezone

    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.broker.base import BrokerError
    from llmtrader.config import Config
    from llmtrader.trader import Decision

    cfg = Config()
    cfg.symbols = ["SPY"]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.cfg = cfg
    broker.events = []
    decision = Decision(action="enter_long", confidence=9, entry=100.0, stop_loss=99.0,
                        take_profit=103.0)
    after_close = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)
    with pytest.raises(BrokerError, match="outside regular trading hours"):
        broker.submit(decision, 1, after_close)


def test_alpaca_refuses_a_sub_share_position_instead_of_rounding_up():
    """At small account sizes the notional cap can allow less than one share. Rounding that up
    to a whole share would silently place a position many times the intended size."""
    from datetime import datetime, timezone

    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.broker.base import BrokerError
    from llmtrader.config import Config
    from llmtrader.trader import Decision

    cfg = Config()
    cfg.symbols = ["SPY"]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.cfg = cfg
    broker.events = []
    broker.api_key = "x"
    broker.secret_key = "y"
    from llmtrader.broker.accounting import LocalAccount

    broker.book = LocalAccount(1000.0, authoritative_equity=True)
    broker.open_entry = None
    broker.live_price = lambda symbol=None: 754.0
    decision = Decision(action="enter_long", confidence=9, entry=754.0, stop_loss=752.7,
                        take_profit=758.0)
    midday = datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)
    with pytest.raises(BrokerError, match="whole"):
        broker.submit(decision, 0.133, midday)


def test_alpaca_reanchors_the_bracket_to_the_live_price():
    """The legs must travel with the fill. Left on the signal price, entries where price ran
    get refused - and those are selectively the ones that would have stopped out."""
    from datetime import datetime, timezone

    from llmtrader.broker.accounting import LocalAccount
    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.config import Config
    from llmtrader.trader import Decision

    cfg = Config()
    cfg.symbols = ["SPY"]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.cfg = cfg
    broker.events = []
    broker.api_key = "x"
    broker.secret_key = "y"
    broker.book = LocalAccount(100000.0, authoritative_equity=True)
    broker.open_entries = {}
    broker.open_entry = None
    broker.trades = []
    broker.live_price = lambda symbol=None: 100.5
    submitted = {}

    class FakeOrder:
        id = "fake-1"

    def fake_submit(req):
        submitted["req"] = req
        return FakeOrder()

    broker.client = type("C", (), {"submit_order": staticmethod(fake_submit)})()
    decision = Decision(action="enter_long", confidence=9, entry=100.0, stop_loss=99.0,
                        take_profit=102.0, size_multiplier=1.0, max_hold_minutes=60)
    decision.symbol = "SPY"
    midday = datetime(2026, 9, 16, 15, 0, tzinfo=timezone.utc)
    broker.submit(decision, 100.0, midday)
    assert decision.stop_loss == 99.5
    assert decision.take_profit == 102.5
    assert abs(decision.entry - decision.stop_loss) == 1.0
    assert abs(decision.take_profit - decision.entry) == 2.0
    assert any(e["event"] == "bracket_reanchored" for e in broker.events)


def test_daily_risk_state_survives_a_restart(tmp_path):
    """A crash must not reset trades_today or day_start_equity, or the daily loss halt silently
    stops working on an unattended run."""
    from datetime import date

    from llmtrader.broker.accounting import LocalAccount

    acct = LocalAccount(100000.0, authoritative_equity=True)
    acct.start_day(date(2026, 9, 16), 100000.0)
    acct.trades_today = 5
    acct.consecutive_losses = 3
    acct.halted = True
    acct.halt_reason = "daily loss limit hit"
    acct.set_balance(98000.0)
    saved = acct.state()

    fresh = LocalAccount(100000.0, authoritative_equity=True)
    assert fresh.restore(saved)
    assert fresh.day == date(2026, 9, 16)
    assert fresh.trades_today == 5
    assert fresh.consecutive_losses == 3
    assert fresh.halted
    assert fresh.day_start_equity == 100000.0
    assert fresh.day_pnl == -2000.0


def test_restore_tolerates_a_bad_state_file():
    from llmtrader.broker.accounting import LocalAccount

    acct = LocalAccount(100000.0)
    assert acct.restore(None) is False
    assert acct.restore({}) is False
    assert acct.restore({"day": "not-a-date", "trades_today": 2}) is True
    assert acct.trades_today == 2
    assert acct.day is None
