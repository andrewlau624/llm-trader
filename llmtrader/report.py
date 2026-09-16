import statistics
from collections import Counter, defaultdict

from .journal import find_runs, open_run


def _safe_mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else 0.0


def summarize_decisions(decisions):
    n = len(decisions)
    if not n:
        return {}
    actions = Counter(d["decision"]["action"] for d in decisions)
    parse_fail = sum(0 if d["decision"].get("parse_ok", True) else 1 for d in decisions)
    confs = [d["decision"]["confidence"] for d in decisions]
    latencies = [d["decision"].get("latency_ms") or 0 for d in decisions]
    taken = sum(1 for d in decisions if d["risk"]["allowed"])
    by_regime = defaultdict(lambda: {"n": 0, "entries": 0})
    for d in decisions:
        r = by_regime[d.get("regime") or "unknown"]
        r["n"] += 1
        if d["risk"]["allowed"]:
            r["entries"] += 1
    return {
        "decisions": n,
        "holds": actions.get("hold", 0),
        "enter_long": actions.get("enter_long", 0),
        "enter_short": actions.get("enter_short", 0),
        "approved_entries": taken,
        "rejected_by_risk": sum(
            1 for d in decisions if not d["risk"]["allowed"] and d["decision"]["action"] != "hold"
        ),
        "parse_failures": parse_fail,
        "avg_confidence": round(_safe_mean(confs), 2),
        "avg_latency_ms": int(_safe_mean(latencies)),
        "by_regime": dict(by_regime),
    }


def summarize_trades(trades):
    n = len(trades)
    if not n:
        return {"trades": 0}
    pnls = [t["pnl"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    reasons = Counter(t["exit_reason"] for t in trades)
    by_confidence = defaultdict(lambda: {"n": 0, "pnl": 0.0})
    for t in trades:
        b = by_confidence[t.get("confidence", 0)]
        b["n"] += 1
        b["pnl"] += t["pnl"]
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100.0, 1),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / n, 2),
        "avg_r": round(_safe_mean(rs), 3),
        "total_r": round(sum(rs), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy": round(sum(pnls) / n, 2),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "avg_hold_min": round(_safe_mean([t.get("hold_min") for t in trades]), 1),
        "exit_reasons": dict(reasons),
        "by_confidence": {k: {"n": v["n"], "pnl": round(v["pnl"], 2)}
                          for k, v in sorted(by_confidence.items())},
    }


def render_report(decisions, trades, title="run"):
    d = summarize_decisions(decisions)
    t = summarize_trades(trades)
    lines = [f"=== REPORT: {title} ===", ""]
    if d:
        lines.append("DECISIONS")
        lines.append(
            f"  windows            {d['decisions']}"
            f"  (holds {d['holds']}, longs {d['enter_long']}, shorts {d['enter_short']})"
        )
        lines.append(
            f"  approved entries   {d['approved_entries']}   "
            f"rejected by risk {d['rejected_by_risk']}   parse failures {d['parse_failures']}"
        )
        lines.append(
            f"  avg confidence     {d['avg_confidence']}/10   avg latency {d['avg_latency_ms']} ms"
        )
        if d.get("by_regime"):
            lines.append("  by regime:")
            for reg, v in sorted(d["by_regime"].items()):
                lines.append(f"    {reg:<14} {v['n']:>4} windows, {v['entries']:>3} entries")
        lines.append("")
    if t.get("trades"):
        lines.append("TRADES")
        lines.append(
            f"  n {t['trades']}  win rate {t['win_rate']}%  ({t['wins']}W/{t['losses']}L)"
            f"  total pnl {t['total_pnl']:+,.2f}  total R {t['total_r']:+.2f}"
        )
        lines.append(
            f"  avg pnl {t['avg_pnl']:+,.2f}  avg R {t['avg_r']:+.3f}"
            f"  profit factor {t['profit_factor']}  expectancy {t['expectancy']:+,.2f}"
        )
        lines.append(
            f"  best {t['best']:+,.2f}  worst {t['worst']:+,.2f}"
            f"  max drawdown {t['max_drawdown']:,.2f}  avg hold {t['avg_hold_min']}m"
        )
        lines.append(f"  exits: {t['exit_reasons']}")
        lines.append("  by confidence:")
        for c, v in t["by_confidence"].items():
            lines.append(f"    confidence {c}: {v['n']} trades, pnl {v['pnl']:+,.2f}")
    else:
        lines.append("TRADES")
        lines.append("  no trades taken")
    return "\n".join(lines)


def report_run(run_dir):
    j = open_run(run_dir)
    return render_report(j.decisions(), j.trades(), title=j.dir.name)


def report_all(base_dir=None):
    runs = find_runs(base_dir)
    if not runs:
        return "no runs found"
    all_decisions, all_trades = [], []
    blocks = []
    for r in runs:
        j = open_run(r)
        dec, tr = j.decisions(), j.trades()
        all_decisions += dec
        all_trades += tr
        blocks.append(render_report(dec, tr, title=r.name))
    blocks.append("")
    blocks.append(render_report(all_decisions, all_trades, title=f"ALL RUNS ({len(runs)})"))
    return "\n\n".join(blocks)
