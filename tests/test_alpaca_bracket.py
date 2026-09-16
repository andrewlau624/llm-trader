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
