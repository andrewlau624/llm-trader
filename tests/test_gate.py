from helpers import make_ctx

from llmtrader.gate import gate_reasons, should_call_llm


def base_ctx(**kw):
    ctx = make_ctx(**kw)
    ctx.session.range_pos_pct = 50.0
    ctx.session.vwap_dist_pct = 0.05
    ctx.session.rvol = 1.0
    ctx.timeframes["5m"].rel_vol = 0.8
    ctx.timeframes["5m"].bb_width_pct = 0.15
    ctx.timeframes["1m"].zscore = 0.2
    ctx.timeframes["5m"].structure = "range"
    ctx.timeframes["5m"].adx = 12.0
    return ctx


def test_gate_disabled_always_calls():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = False
    call, notes = should_call_llm(base_ctx(), cfg)
    assert call
    assert notes == ["gate disabled"]


def test_gate_closed_when_nothing_is_happening():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    call, notes = should_call_llm(base_ctx(), cfg)
    assert not call
    assert "no directional trigger" in notes[0]


def test_gate_closed_when_trend_without_participation():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    ctx = base_ctx()
    ctx.timeframes["5m"].adx = 34.0
    call, notes = should_call_llm(ctx, cfg)
    assert not call
    assert "nothing is happening" in notes[0]


def test_gate_open_when_trend_has_volume():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    ctx = base_ctx()
    ctx.timeframes["5m"].adx = 34.0
    ctx.timeframes["5m"].rel_vol = 1.9
    call, notes = should_call_llm(ctx, cfg)
    assert call
    assert any("adx" in n for n in notes)
    assert any("volume" in n for n in notes)


def test_gate_open_on_stretched_zscore_with_session_extreme():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    ctx = base_ctx()
    ctx.timeframes["1m"].zscore = -2.4
    ctx.session.range_pos_pct = 5.0
    call, notes = should_call_llm(ctx, cfg)
    assert call
    assert any("zscore" in n for n in notes)
    assert any("extreme" in n for n in notes)


def test_gate_open_on_clean_swing_structure_and_vwap_distance():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    ctx = base_ctx()
    ctx.timeframes["5m"].structure = "lh_ll"
    ctx.session.vwap_dist_pct = -0.9
    call, notes = should_call_llm(ctx, cfg)
    assert call
    assert any("structure" in n for n in notes)
    assert any("vwap" in n for n in notes)


def test_gate_reasons_are_flat_list():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    cfg.llm_gate = True
    ctx = base_ctx()
    ctx.timeframes["5m"].adx = 40.0
    ctx.timeframes["5m"].rel_vol = 2.5
    assert len(gate_reasons(ctx, cfg)) >= 2
