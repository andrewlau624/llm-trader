"""Did real execution leave the edge intact?

The backtest says the reversal rule earns about 15.6 bps per trade and breaks even at roughly 16 bps
round trip, against a modelled 3 bps of slippage. A week of live paper fills replaces that guess
with a measurement. This reports the measurement, the realized trades, and the comparison.

    make week-report
"""

import glob
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ET = ZoneInfo("America/New_York")
BACKTEST_EDGE_BPS = 15.6
BREAK_EVEN_BPS = 16.0


def load_runs(mode="paper"):
    runs = sorted(glob.glob(f"runs/{mode}-*"))
    out = []
    for path in runs:
        d = Path(path)
        rows = {"decisions": [], "trades": [], "events": []}
        for name, key in (("decisions.jsonl", "decisions"), ("trades.jsonl", "trades"),
                          ("events.jsonl", "events")):
            f = d / name
            if not f.exists():
                continue
            for line in f.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows[key].append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if rows["decisions"] or rows["trades"] or rows["events"]:
            meta = {}
            mf = d / "meta.json"
            if mf.exists():
                try:
                    meta = json.loads(mf.read_text())
                except json.JSONDecodeError:
                    meta = {}
            out.append((d, meta, rows))
    return out


def pctile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(p / 100.0 * (len(ordered) - 1))))]


def main():
    runs = load_runs()
    if not runs:
        print("\n  no paper runs found")
        return 0

    decisions = [r for _, _, rows in runs for r in rows["decisions"]]
    trades = [r for _, _, rows in runs for r in rows["trades"]]
    events = [e for _, _, rows in runs for e in rows["events"]]

    print(f"\n  paper runs: {len(runs)}")
    first = min((d.name for d, _, _ in runs), default="?")
    last = max((d.name for d, _, _ in runs), default="?")
    print(f"  from {first} to {last}")

    strategies = Counter(m.get("strategy") or ("deterministic" if (m.get("broker") and
                        "deterministic" in json.dumps(m)) else "llm") for _, m, _ in runs)
    brokers = Counter(m.get("broker", "?") for _, m, _ in runs)
    print(f"  brokers {dict(brokers)}   strategies {dict(strategies)}")

    kinds = Counter(d.get("skipped", "evaluated") for d in decisions)
    print("\n  --- activity ---")
    print(f"  decision cycles          {len(decisions)}")
    for kind, n in sorted(kinds.items()):
        print(f"    {kind:<22} {n}")
    submitted = sum(1 for d in decisions if d.get("risk", {}).get("allowed"))
    refused = sum(1 for d in decisions
                  if not d.get("risk", {}).get("allowed")
                  and d.get("decision", {}).get("action") != "hold")
    print(f"  entries submitted        {submitted}")
    print(f"  entries refused by risk  {refused}")

    refusals = Counter()
    for d in decisions:
        if not d.get("risk", {}).get("allowed") and d.get("decision", {}).get("action") != "hold":
            for reason in d["risk"].get("reasons", []):
                refusals[reason.split("(")[0].strip()[:60]] += 1
    for reason, n in refusals.most_common(6):
        print(f"    refused: {reason}  x{n}")

    print("\n  --- the number that decides it: execution cost vs the signal price ---")
    fills = [e for e in events if e.get("event") == "fill_vs_signal"]
    reanchor = [e for e in events if e.get("event") == "bracket_reanchored"]
    if reanchor:
        drift = [abs(e["drift_bps"]) for e in reanchor]
        print(f"  latency only (signal -> live price at submission), n={len(reanchor)}: "
              f"median {statistics.median(drift):.2f} bps, p90 {pctile(drift, 90):.2f}")
    if not fills:
        print("  no filled entries yet - nothing to measure")
        if reanchor:
            print("  (re-anchoring events exist, so entries were submitted but did not fill)")
    else:
        costs = [e["cost_bps"] for e in fills]
        per_symbol = defaultdict(list)
        for e in fills:
            per_symbol[e["symbol"]].append(e["cost_bps"])
        print(f"  FILL vs SIGNAL, n={len(fills)} round trips")
        print(f"    mean   {statistics.mean(costs):+.2f} bps")
        print(f"    median {statistics.median(costs):+.2f} bps")
        print(f"    p10 {pctile(costs, 10):+.2f}   p90 {pctile(costs, 90):+.2f}   "
              f"worst {max(costs):+.2f}")
        for sym, vals in sorted(per_symbol.items()):
            print(f"    {sym:<6} n={len(vals):<4} mean {statistics.mean(vals):+.2f} bps")
        modelled = [e.get("modelled_bps") for e in fills if e.get("modelled_bps") is not None]
        if modelled:
            print(f"    modelled assumption was {statistics.mean(modelled):.2f} bps per side")

    print("\n  --- realized trades ---")
    if not trades:
        print("  none yet")
    else:
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        exits = Counter(t["exit_reason"] for t in trades)
        per_trade_bps = []
        for t in trades:
            notional = abs(t["entry"] * t["qty"])
            if notional:
                per_trade_bps.append(t["pnl"] / notional * 10000.0)
        print(f"  n {len(trades)}  win rate {len(wins) / len(trades) * 100:.1f}%  "
              f"net {sum(pnls):+,.2f}")
        print(f"  avg R {statistics.mean([t['r_multiple'] for t in trades]):+.3f}  "
              f"avg hold {statistics.mean([t['hold_min'] for t in trades]):.0f}m")
        print(f"  exits {dict(exits)}")
        by_symbol = defaultdict(lambda: [0, 0.0])
        for t in trades:
            by_symbol[t["symbol"]][0] += 1
            by_symbol[t["symbol"]][1] += t["pnl"]
        print("  by symbol: " + " | ".join(
            f"{s} {n}x {p:+,.0f}" for s, (n, p) in sorted(by_symbol.items())))
        if per_trade_bps:
            print(f"\n  REALIZED EDGE {statistics.mean(per_trade_bps):+.2f} bps/trade")
            print(f"  backtest claimed {BACKTEST_EDGE_BPS:+.1f} bps/trade; "
                  f"break-even is about {BREAK_EVEN_BPS:.0f} bps round trip")
            edge = statistics.mean(per_trade_bps)
            if edge > BREAK_EVEN_BPS:
                print("  verdict: realized edge is above break-even on this sample")
            elif edge > 0:
                print("  verdict: positive but thin - inside the range one week cannot resolve")
            else:
                print("  verdict: negative. Check the execution cost above before blaming")
                print("  the signal")
        if len(trades) < 30:
            print(f"\n  note: {len(trades)} trades resolves almost nothing statistically.")
            print("  A week is enough to measure EXECUTION COST, which is stable, not edge.")

    print("\n  --- anything broken ---")
    notable = [e for e in events if e.get("event") in
               ("entry_not_placed", "order_rejected", "bracket_rejected",
                "live_price_unavailable", "error")]
    if not notable:
        print("  no rejections or errors recorded")
    for e in notable[:10]:
        detail = str(e.get("error") or e.get("reason") or "")[:100]
        print(f"    {e.get('event')}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
