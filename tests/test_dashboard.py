from helpers import make_account, make_ctx

from llmtrader.dashboard import render_dashboard


def test_dashboard_contains_all_sections():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    text = render_dashboard(make_ctx(), account=make_account(), cfg=cfg,
                            recent=["14:30 hold conf 5"])
    assert "=== MARKET DASHBOARD :: TEST ===" in text
    assert "Regime    : TREND_UP" in text
    assert "TIMEFRAME MATRIX" in text
    assert "SESSION" in text
    assert "KEY LEVELS" in text
    assert "CROSS MARKET" in text
    assert "ACCOUNT" in text
    assert "RECENT DECISIONS" in text
    assert "YOUR JOB" in text
    for tf in ("1m", "5m", "1h"):
        assert f"\n{tf} " in text


def test_dashboard_stays_small_for_prompt_budget():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    text = render_dashboard(make_ctx(), account=make_account(), cfg=cfg)
    assert len(text) < 4500


def test_dashboard_shows_open_position_and_halt():
    from datetime import datetime, timezone

    from llmtrader.broker.base import Position

    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    pos = Position(symbol="TEST", side="short", qty=12, entry=101.5, stop=102.5,
                   take_profit=98.5, opened_at=datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc))
    acct = make_account(position=pos, trades_today=2, consecutive_losses=1)
    text = render_dashboard(make_ctx(), account=acct, cfg=cfg)
    assert "SHORT 12 @ 101.50" in text
    assert "trades today 2/6" in text
    assert "consecutive losses 1" in text

    acct2 = make_account(halted=True, halt_reason="daily loss limit hit (-1001 <= -1000)")
    text2 = render_dashboard(make_ctx(), account=acct2, cfg=cfg)
    assert "halted: YES - daily loss limit hit" in text2


def test_dashboard_without_account_omits_account_block():
    cfg = __import__("llmtrader.config", fromlist=["Config"]).Config()
    text = render_dashboard(make_ctx(), cfg=cfg)
    assert "--- ACCOUNT ---" not in text
    assert "--- RECENT DECISIONS ---" not in text
