"""Turn a study's window rows into the compact priors table the prompt quotes."""

from datetime import datetime, timezone

LABEL_ORDER = ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR")


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _t(xs):
    import math
    import statistics

    xs = list(xs)
    if len(xs) < 3:
        return 0.0
    sd = statistics.stdev(xs)
    if sd == 0:
        return 0.0
    return _mean(xs) / (sd / math.sqrt(len(xs)))


def _rule_stats(rows, predicate, side, horizon):
    picked = [r for r in rows if predicate(r)]
    vals = []
    for r in picked:
        if horizon not in r.get("excess", {}):
            continue
        if side == "trend":
            sign = 1.0 if r["trend"] >= 0 else -1.0
        elif side == "fade":
            sign = -1.0 if r["trend"] >= 0 else 1.0
        else:
            sign = 1.0 if side == "long" else -1.0
        vals.append(sign * r["excess"][horizon])
    if not vals:
        return None
    return {"n": len(picked), "excess_bps": round(_mean(vals), 2), "t": round(_t(vals), 2)}


def build_priors(rows, horizons, symbol, granularity, cost_bps=2.0):
    horizon = max(horizons)
    days = sorted({r["day"] for r in rows})
    mid = len(days) // 2
    first, second = set(days[:mid]), set(days[mid:])
    h1 = [r for r in rows if r["day"] in first]
    h2 = [r for r in rows if r["day"] in second]

    labels = {}
    for name in LABEL_ORDER:
        group = [r for r in rows if r["trend_label"] == name]
        if not group:
            continue
        vals = [r["excess"][horizon] for r in group if horizon in r.get("excess", {})]
        if not vals:
            continue
        labels[name] = {
            "n": len(group),
            "excess_bps": round(_mean(vals), 2),
            "t": round(_t(vals), 2),
            "win": round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1),
        }

    rule_defs = [
        ("fade adx>25 with volume>1.2",
         lambda r: (r.get("adx5") or 0) > 25 and r["volume"] > 1.2, "fade"),
        ("MR>70 snap DOWN -> short",
         lambda r: r["mr"] > 70 and r["mr_snap"] == "DOWN", "short"),
        ("MR>70 snap UP -> long",
         lambda r: r["mr"] > 70 and r["mr_snap"] == "UP", "long"),
        ("volatility HIGH -> long",
         lambda r: r["volatility"] == "HIGH", "long"),
        ("volatility LOW -> long",
         lambda r: r["volatility"] == "LOW", "long"),
        ("follow BULL + momentum UP",
         lambda r: r["trend_label"] in ("BULL", "STRONG_BULL")
         and r["momentum_label"] in ("UP", "STRONG_UP"), "long"),
        ("follow BEAR + momentum DOWN",
         lambda r: r["trend_label"] in ("BEAR", "STRONG_BEAR")
         and r["momentum_label"] in ("DOWN", "STRONG_DOWN"), "short"),
    ]
    rules = []
    for name, predicate, side in rule_defs:
        overall = _rule_stats(rows, predicate, side, horizon)
        if not overall:
            continue
        a = _rule_stats(h1, predicate, side, horizon) or {}
        b = _rule_stats(h2, predicate, side, horizon) or {}
        if a.get("excess_bps") is None or b.get("excess_bps") is None:
            continue
        if (a["excess_bps"] > 0) != (b["excess_bps"] > 0):
            continue
        overall.update({"name": name, "h1": a["excess_bps"], "h2": b["excess_bps"]})
        rules.append(overall)
    rules.sort(key=lambda r: -abs(r["excess_bps"]))

    return {
        "symbol": symbol,
        "granularity": granularity,
        "sessions": len(days),
        "windows": len(rows),
        "horizon_min": horizon,
        "cost_bps": cost_bps,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "first_half": [days[0], days[mid - 1]] if days else [],
        "second_half": [days[mid], days[-1]] if days else [],
        "labels": labels,
        "rules": rules,
    }
