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


def test_alpaca_constructor_initialises_before_restoring_state(monkeypatch, tmp_path):
    """Regression: _restore_state ran before self.events existed, so the constructor raised and
    no live process could start at all. Nothing caught it because the other tests bypass __init__."""
    from alpaca.trading.client import TradingClient

    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.config import Config

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_account(self):
            return type("A", (), {"equity": "100000", "cash": "100000", "status": "ACTIVE"})()

    monkeypatch.setattr(TradingClient, "__init__", FakeClient.__init__)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    cfg = Config()
    cfg.symbols = ["SPY"]
    broker = AlpacaBroker(cfg, state_dir=tmp_path)
    assert broker.events == []
    assert broker.book.day is None
    assert broker.state_path.parent == tmp_path
    assert broker.state_path.name.startswith("book-")


def test_account_lock_refuses_a_second_holder(tmp_path):
    """The bug that cost a stop order: two live loops on one account, each cancelling the other's
    orders at the session-end close."""
    from llmtrader.lock import AccountLock

    path = tmp_path / "live.lock"
    first = AccountLock(path)
    assert first.acquire() is None
    second = AccountLock(path)
    holder = second.acquire()
    assert holder is not None and "held by" in holder
    first.release()
    third = AccountLock(path)
    assert third.acquire() is None
    third.release()


def test_account_fingerprint_distinguishes_accounts():
    from llmtrader.lock import account_fingerprint

    a = account_fingerprint("key-a", "secret")
    assert a == account_fingerprint("key-a", "secret")
    assert a != account_fingerprint("key-b", "secret")
    assert "key-a" not in a


def test_state_file_is_per_account_and_isolated(tmp_path):
    from alpaca.trading.client import TradingClient

    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.config import Config

    class FakeClient:
        def __init__(self, *a, **k):
            pass

    orig = TradingClient.__init__
    TradingClient.__init__ = FakeClient.__init__
    try:
        import os

        os.environ["ALPACA_API_KEY"] = "k1"
        os.environ["ALPACA_SECRET_KEY"] = "s1"
        cfg = Config()
        cfg.symbols = ["SPY"]
        b1 = AlpacaBroker(cfg, api_key="k1", secret_key="s1", state_dir=tmp_path)
        b2 = AlpacaBroker(cfg, api_key="k2", secret_key="s2", state_dir=tmp_path)
        assert b1.state_path != b2.state_path
        assert b1.state_path.parent == tmp_path
        assert b1.events == []
    finally:
        TradingClient.__init__ = orig


def test_service_unit_prevents_a_restart_loop_on_lock_conflict():
    """Restart=always plus exit-on-lock-conflict would spin forever. The unit must stop instead."""
    from pathlib import Path

    unit = Path("deploy/llm-trader.service.in").read_text()
    assert "RestartPreventExitStatus=2" in unit
    live = Path("scripts/live.py").read_text()
    assert "return 2" in live


def test_service_unit_lets_journald_capture_output():
    """Redirecting stdout to a file made `make service-logs` show nothing but systemd noise,
    which is how you spend a week unable to see what the bot decided."""
    from pathlib import Path

    unit = Path("deploy/llm-trader.service.in").read_text()
    assert "StandardOutput=append:" not in unit
    assert "StandardError=append:" not in unit


def test_unprotected_positions_flags_a_naked_holding():
    """The state that must never survive a session boundary: open position, no stop, no close.

    Built with __new__ rather than patching __init__, because patching only the constructor leaves
    the real API methods in place and the test then talks to Alpaca.
    """
    from llmtrader.broker.alpaca import AlpacaBroker
    from llmtrader.config import Config

    class FakePos:
        symbol = "TQQQ"
        qty = "-1398"
        avg_entry_price = "71.45"
        unrealized_pl = "-1269"

    class FakeOrder:
        def __init__(self, t):
            self.type = t

    class FakeClient:
        def __init__(self, orders):
            self.orders = orders

        def get_open_position(self, symbol):
            return FakePos()

        def get_orders(self, req=None):
            return self.orders

        def get_all_positions(self):
            return [FakePos()]

    cfg = Config()
    cfg.symbols = ["TQQQ"]
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker.cfg = cfg
    broker.events = []

    broker.client = FakeClient([])
    assert broker.unprotected_positions() == ["TQQQ"]
    assert broker.position_report()[0]["protected"] is False

    broker.client = FakeClient([FakeOrder("OrderType.MARKET")])
    assert broker.unprotected_positions() == []
    assert broker.position_report()[0]["protected"] is True

    broker.client = FakeClient([FakeOrder("OrderType.STOP")])
    assert broker.unprotected_positions() == []
