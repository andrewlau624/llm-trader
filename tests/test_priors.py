import json

from llmtrader.analysis import build_priors
from llmtrader.priors import load_priors, render_priors


def row(day, label, excess, trend=0.0, momentum_label="FLAT", mr=0.0, snap="FLAT",
        volatility="NORMAL", volume=1.0, adx5=20.0):
    return {
        "day": day,
        "trend_label": label,
        "trend": trend,
        "momentum_label": momentum_label,
        "mr": mr,
        "mr_snap": snap,
        "volatility": volatility,
        "volume": volume,
        "adx5": adx5,
        "excess": {60: excess},
        "fwd": {60: excess},
    }


def test_build_priors_reports_label_resolution_directionally():
    rows = [row(f"2026-06-{d:02d}", "BULL", -4.0, trend=40) for d in range(1, 6)]
    rows += [row(f"2026-06-{d:02d}", "BEAR", 5.0, trend=-40) for d in range(6, 11)]
    priors = build_priors(rows, [60], "SPY", "5m")
    assert priors["windows"] == 10
    assert priors["labels"]["BULL"]["excess_bps"] == -4.0
    assert priors["labels"]["BEAR"]["excess_bps"] == 5.0


def test_build_priors_keeps_only_rules_that_agree_across_halves():
    agreeing = [
        row(f"2026-06-{d:02d}", "NEUTRAL", -4.0, adx5=40, volume=2.0) for d in range(1, 6)
    ]
    disagreeing = [
        row(f"2026-06-{d:02d}", "NEUTRAL", 4.0, adx5=40, volume=2.0) for d in range(6, 11)
    ]
    priors = build_priors(agreeing, [60], "SPY", "5m")
    names = [r["name"] for r in priors["rules"]]
    assert "fade adx>25 with volume>1.2" in names
    priors2 = build_priors(agreeing + disagreeing, [60], "SPY", "5m")
    names2 = [r["name"] for r in priors2["rules"]]
    assert "fade adx>25 with volume>1.2" not in names2


def test_build_priors_records_both_halves():
    rows = [
        row(f"2026-06-{d:02d}", "BULL", -4.0, trend=40, momentum_label="UP")
        for d in range(1, 11)
    ]
    priors = build_priors(rows, [60], "SPY", "5m")
    rule = next(r for r in priors["rules"] if r["name"] == "follow BULL + momentum UP")
    assert rule["h1"] is not None and rule["h2"] is not None


def test_render_priors_is_ordered_and_honest():
    priors = {
        "symbol": "SPY",
        "granularity": "5m",
        "sessions": 60,
        "windows": 3940,
        "horizon_min": 60,
        "cost_bps": 2.0,
        "labels": {
            "STRONG_BULL": {"n": 251, "excess_bps": -6.68, "t": -6.01, "win": 33.9},
            "BEAR": {"n": 1060, "excess_bps": 4.69, "t": 7.39, "win": 60.0},
        },
        "rules": [
            {"name": "fade adx>25 with volume>1.2", "n": 283, "excess_bps": 9.39, "t": 6.13,
             "h1": 14.28, "h2": 4.4},
        ],
    }
    text = "\n".join(render_priors(priors))
    assert "STRONG_BULL" in text and "BEAR" in text
    assert text.index("STRONG_BULL") < text.index("BEAR")
    assert "CONTRARIAN" in text
    assert "factor of three" in text
    assert "not a promise" in text
    assert "not that a trade will work" in text


def test_render_priors_handles_nothing():
    assert render_priors(None) == []


def test_load_priors_missing_and_malformed(tmp_path):
    assert load_priors(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_priors(bad) is None
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"symbol": "SPY"}))
    assert load_priors(good)["symbol"] == "SPY"
