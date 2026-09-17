"""Monte Carlo over the trade sequence.

The backtest gives one path. One path tells you nothing about the range of outcomes, and the range
is what decides whether a strategy is survivable. This resamples the observed trades to estimate:

  * the distribution of returns and of maximum drawdown
  * the probability the edge is actually positive, given how few trades we have
  * how much the answer moves when the cost assumption is wrong, which is the single largest
    unmodelled risk here

Three resampling schemes, because they answer different questions:

  iid          shuffle individual trades. The standard bootstrap, and it assumes trade order does
               not matter.
  block        resample runs of consecutive trades, preserving short-run streaks. If the iid and
               block results disagree, trade outcomes are autocorrelated and the iid drawdowns are
               too optimistic.
  stationary   block bootstrap with random block lengths, which is the least assumption-heavy of
               the three.

None of them can see a regime change, because the only information available is the trades that
already happened. A strategy that stops working in a new regime will look fine to all three.
"""

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEED = 20260916


def load_trades(path):
    data = json.loads(Path(path).read_text())
    if not data:
        raise SystemExit(f"{path} has no trades")
    return data


def pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round(p / 100.0 * (len(ordered) - 1))))
    return ordered[idx]


def path_stats(pnls, equity):
    curve = peak = dd = 0.0
    for p in pnls:
        curve += p
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    return {
        "net": sum(pnls),
        "return_pct": sum(pnls) / equity * 100.0,
        "max_dd": dd,
        "max_dd_pct": dd / equity * 100.0,
    }


def simulate(pnls, equity, n_paths, n_trades, mode, rng):
    results = []
    n = len(pnls)
    for _ in range(n_paths):
        if mode == "iid":
            path = [pnls[rng.randrange(n)] for _ in range(n_trades)]
        elif mode == "block":
            block = 10
            path = []
            while len(path) < n_trades:
                start = rng.randrange(max(1, n - block))
                path.extend(pnls[start:start + block])
            path = path[:n_trades]
        else:  # stationary: restart the block origin every time (Politis-Romano)
            mean_block = 10
            path = []
            while len(path) < n_trades:
                start = rng.randrange(n)
                length = max(1, int(rng.expovariate(1.0 / mean_block)))
                for k in range(length):
                    path.append(pnls[(start + k) % n])
                    if len(path) >= n_trades:
                        break
            path = path[:n_trades]
        results.append(path_stats(path, equity))
    return results


def report(label, results, equity):
    rets = [r["return_pct"] for r in results]
    dds = [r["max_dd_pct"] for r in results]
    losses = sum(1 for x in rets if x < 0) / len(rets) * 100.0
    dd10 = sum(1 for x in dds if x >= 10) / len(dds) * 100.0
    dd20 = sum(1 for x in dds if x >= 20) / len(dds) * 100.0
    print(f"\n  {label}")
    print(f"    return %    p5 {pct(rets, 5):+7.2f}  p25 {pct(rets, 25):+7.2f}  "
          f"median {pct(rets, 50):+7.2f}  p75 {pct(rets, 75):+7.2f}  p95 {pct(rets, 95):+7.2f}")
    print(f"    max dd %    p50 {pct(dds, 50):7.2f}  p75 {pct(dds, 75):7.2f}  "
          f"p95 {pct(dds, 95):7.2f}  worst {max(dds):7.2f}")
    print(f"    P(losing period) {losses:5.1f}%   P(dd >= 10%) {dd10:5.1f}%   "
          f"P(dd >= 20%) {dd20:5.1f}%")
    return {"p5": pct(rets, 5), "p50": pct(rets, 50), "p95": pct(rets, 95),
            "dd50": pct(dds, 50), "dd95": pct(dds, 95), "losses": losses}


def edge_uncertainty(pnls, n_paths, n_trades, rng):
    """How often does a resampled path have a non-positive mean trade?

    With ~300 trades this is the honest answer to "is the edge real?", and it is not the same
    question as whether the backtest was profitable.
    """
    negative = 0
    for _ in range(n_paths):
        path = [pnls[rng.randrange(len(pnls))] for _ in range(n_trades)]
        if statistics.mean(path) <= 0:
            negative += 1
    return negative / n_paths * 100.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Monte Carlo over a trade sequence")
    ap.add_argument("--trades", default="runs/scan-trades.json")
    ap.add_argument("--equity", type=float, default=100000.0)
    ap.add_argument("--paths", type=int, default=20000)
    ap.add_argument("--horizon-months", type=float, default=None,
                    help="scale the path length to this many months of trading, using the "
                         "observed trades-per-session rate")
    ap.add_argument("--trades-per-path", type=int, default=None,
                    help="default: the observed count, i.e. the same 60-session period")
    ap.add_argument("--horizon-trades", type=int, default=None,
                    help="override to model a longer horizon, e.g. 1100 for ~12 months")
    args = ap.parse_args(argv)

    trades = load_trades(args.trades)
    pnls = [t["pnl"] for t in trades]
    notionals = [abs(t["entry"] * t["qty"]) for t in trades]
    n_trades = args.horizon_trades or args.trades_per_path or len(pnls)
    rng = random.Random(SEED)

    mean_bps = statistics.mean([p / n for p, n in zip(pnls, notionals, strict=True)]) * 10000
    print(f"\nmonte carlo: {len(pnls)} observed trades from {args.trades}")
    print(f"  observed net {sum(pnls):+,.2f} on ${args.equity:,.0f} "
          f"({sum(pnls) / args.equity * 100:+.2f}%), avg {statistics.mean(pnls):+,.2f}/trade")
    print(f"  avg notional ${statistics.mean(notionals):,.0f}, "
          f"avg edge {mean_bps:+.2f} bps/trade")
    print(f"  resampling {n_trades} trades per path, {args.paths:,} paths")

    print("\n  === assuming the observed edge is the true edge ===")
    for mode in ("iid", "block", "stationary"):
        report(f"{mode} bootstrap", simulate(pnls, args.equity, args.paths, n_trades, mode, rng),
               args.equity)

    print("\n  === if execution costs more than modelled ===")
    print(f"  (edge is {mean_bps:.1f} bps/trade; backtest break-even was ~16 bps round trip)")
    for extra in (0.0, 2.0, 4.0, 6.0, 10.0):
        shifted = [p - extra / 10000.0 * n for p, n in zip(pnls, notionals, strict=True)]
        base = simulate(shifted, args.equity, args.paths // 4, n_trades, "iid", rng)
        report(f"costs {extra:.0f} bps worse per round trip", base, args.equity)

    print("\n  === if the true edge is smaller (or absent) ===")
    print("  Mean-shifted, not scaled: shifting preserves the dispersion, which is what sets the")
    print("  drawdowns. Scaling by zero would erase the variance and quietly report zero risk.")
    mu = statistics.mean(pnls)
    for label, keep in (("all of it", 1.0), ("half of it", 0.5), ("none of it (the null)", 0.0),
                        ("the signal is inverted", -1.0)):
        shifted_pnls = [p - (1.0 - keep) * mu for p in pnls]
        base = simulate(shifted_pnls, args.equity, args.paths // 4, n_trades, "iid", rng)
        report(f"edge: {label}  (avg {statistics.mean(shifted_pnls):+,.2f}/trade)",
               base, args.equity)

    print("\n  === is the edge distinguishable from zero? ===")
    rng2 = random.Random(SEED + 1)
    p_neg = edge_uncertainty(pnls, args.paths, n_trades, rng2)
    print(f"    P(mean trade <= 0) = {p_neg:.1f}%")
    print(f"    observed mean {statistics.mean(pnls):+,.3f}/trade, "
          f"std {statistics.stdev(pnls):.3f}, "
          f"t = {statistics.mean(pnls) / (statistics.stdev(pnls) / len(pnls) ** 0.5):+.2f}")

    print("\n  Caveats that no amount of resampling fixes:")
    print("    - The trades all come from one 60-session regime. Bootstrapping cannot invent a")
    print("      regime the sample never saw, so every drawdown here is a lower bound.")
    print("    - The trade sequence is not stationary: position sizing, liquidity and correlation")
    print("      all change with account size, which the resampler holds fixed.")
    print("    - Costs are the dominant risk, and they are modelled, not measured. The live loop")
    print("      now records real fill-versus-signal slippage; replace this assumption with it")
    print("      as soon as there are fills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
