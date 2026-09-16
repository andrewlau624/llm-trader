import json

import pytest

from llmtrader.trader import Decision, extract_json, parse_decision


def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_prose_around():
    text = 'Sure, here is my decision:\n{"action": "hold"} and that is all.'
    assert extract_json(text) == {"action": "hold"}


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_parse_action_aliases():
    assert parse_decision('{"action": "BUY"}').action == "enter_long"
    assert parse_decision('{"action": "sell"}').action == "enter_short"
    assert parse_decision('{"action": "wait"}').action == "hold"
    assert parse_decision('{"action": "SHORT"}').action == "enter_short"


def test_parse_hold_nulls_price_fields():
    d = parse_decision(
        '{"action":"hold","confidence":4,"entry":100,"stop_loss":99,"take_profit":102,'
        '"size_multiplier":1.0,"max_hold_minutes":30,"thesis":"x","invalidation":"y"}'
    )
    assert d.action == "hold"
    assert d.entry is None and d.stop_loss is None and d.take_profit is None


def test_parse_coerces_string_numbers_and_clamps():
    d = parse_decision(
        '{"action":"enter_long","confidence":"11","entry":"1,234.5","stop_loss":"1230",'
        '"take_profit":"1240","size_multiplier":"9","max_hold_minutes":"900",'
        '"thesis":"t","invalidation":"i"}'
    )
    assert d.confidence == 10
    assert d.entry == 1234.5
    assert d.size_multiplier == 2.0
    assert d.max_hold_minutes == 240
    assert d.parse_ok


def test_parse_bad_json_marks_failure():
    d = parse_decision("I think we should go long")
    assert not d.parse_ok
    assert d.action == "hold"


def test_reward_risk():
    d = Decision(action="enter_long", entry=100.0, stop_loss=99.0, take_profit=103.0)
    assert d.reward_risk() == pytest.approx(3.0)
    assert d.side == "long"
    d = Decision(action="enter_short", entry=100.0, stop_loss=101.0, take_profit=97.0)
    assert d.reward_risk() == pytest.approx(3.0)
    assert d.side == "short"


def test_semantic_problems_flags_missing_and_wrong_side():
    from llmtrader.config import Config
    from llmtrader.llm import MockClient
    from llmtrader.trader import Trader

    tr = Trader(MockClient(), Config())
    problems = tr.semantic_problems(Decision(action="enter_long", entry=100.0, stop_loss=None,
                                             take_profit=None))
    assert any("stop_loss is null" in p for p in problems)
    problems = tr.semantic_problems(
        Decision(action="enter_long", entry=100.0, stop_loss=101.0, take_profit=103.0)
    )
    assert any("wrong side" in p for p in problems)
    problems = tr.semantic_problems(
        Decision(action="enter_long", entry=100.0, stop_loss=99.0, take_profit=100.5)
    )
    assert any("reward:risk" in p for p in problems)
    assert tr.semantic_problems(
        Decision(action="enter_long", entry=100.0, stop_loss=99.0, take_profit=103.0)
    ) == []


def test_trader_retries_until_semantically_valid():
    from datetime import datetime, timedelta, timezone

    from llmtrader.config import Config
    from llmtrader.context import build_context
    from llmtrader.data.base import Bar
    from llmtrader.llm import MockClient
    from llmtrader.trader import Trader

    start = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    for i in range(120):
        price += 0.05
        bars.append(
            Bar(
                ts=start + timedelta(minutes=i),
                open=price - 0.05,
                high=price + 0.1,
                low=price - 0.1,
                close=price,
                volume=1000 + i,
                symbol="TEST",
            )
        )
    ctx = build_context("TEST", bars, bars[-1].ts)
    src = ctx.price
    responses = [
        json.dumps({"action": "enter_long", "confidence": 8, "entry": src, "stop_loss": None,
                    "take_profit": None, "size_multiplier": 1.0, "max_hold_minutes": 30,
                    "thesis": "t", "invalidation": "i"}),
        json.dumps({"action": "enter_long", "confidence": 8, "entry": src,
                    "stop_loss": round(src - 1, 2), "take_profit": round(src + 0.2, 2),
                    "size_multiplier": 1.0, "max_hold_minutes": 30, "thesis": "t",
                    "invalidation": "i"}),
        json.dumps({"action": "enter_long", "confidence": 8, "entry": src,
                    "stop_loss": round(src - 1, 2), "take_profit": round(src + 3, 2),
                    "size_multiplier": 1.0, "max_hold_minutes": 30, "thesis": "t",
                    "invalidation": "i"}),
    ]
    cfg = Config()
    cfg.llm_retries = 3
    tr = Trader(MockClient(responses=responses), cfg)
    decision, _ = tr.decide(ctx)
    assert decision.attempts == 3
    assert decision.parse_ok
    assert decision.reward_risk() >= 1.5


def test_trader_gives_up_after_max_attempts():
    from datetime import datetime, timedelta, timezone

    from llmtrader.config import Config
    from llmtrader.context import build_context
    from llmtrader.data.base import Bar
    from llmtrader.llm import MockClient
    from llmtrader.trader import Trader

    start = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)
    bars = [
        Bar(ts=start + timedelta(minutes=i), open=100, high=100.1, low=99.9, close=100,
            volume=1000, symbol="TEST")
        for i in range(120)
    ]
    ctx = build_context("TEST", bars, bars[-1].ts)
    cfg = Config()
    cfg.llm_retries = 1
    tr = Trader(MockClient(responses=['{"action": "enter_long", "confidence": 8, "entry": 100}']), cfg)
    decision, _ = tr.decide(ctx)
    assert decision.action == "hold"
    assert not decision.semantic_ok
    assert decision.parse_ok
