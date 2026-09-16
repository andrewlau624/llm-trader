"""What could this actually make, and what would it cost to find out?

Feeds three measured-or-assumed quantities into an explicit model instead of a vibe:

  * edge per trade, in basis points, taken from the signal study (already net of costs)
  * trades per day, from the observed rate in replays
  * the fixed monthly cost of running it (hosting and tokens)

It then reports the thing almost nobody computes: how long you would have to run before the
result is distinguishable from luck. With a small edge and normal variance, that is usually the
number that decides whether a deployment is worth starting.
"""

import argparse
import math


def monthly(equity, notional, edge_bps, trades_per_day, trading_days=21):
    per_trade = notional * edge_bps / 10000.0
    gross = per_trade * trades_per_day * trading_days
    return {"per_trade": per_trade, "gross_month": gross,
            "pct_month": gross / equity * 100.0 if equity else 0.0}


def noise(equity, notional, stop_pct, trades_per_day, trading_days=21):
    """Standard deviation of monthly P&L if every trade is a coin flip at the given stop size.

    A stop of stop_pct with roughly even odds means each trade is +/- that much times the
    notional, so monthly variance scales with sqrt(trades).
    """
    per_trade_sd = notional * stop_pct
    daily_sd = per_trade_sd * math.sqrt(trades_per_day)
    return per_trade_sd, daily_sd * math.sqrt(trading_days)


def months_to_significance(monthly_edge_dollars, monthly_sd_dollars, target_t=2.0):
    if monthly_edge_dollars <= 0:
        return None
    return (target_t * monthly_sd_dollars / monthly_edge_dollars) ** 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="Economics of running this thing")
    ap.add_argument("--equity", type=float, required=True, help="account size in dollars")
    ap.add_argument("--edge-bps", type=float, default=5.0,
                    help="measured excess edge per trade, net of costs (study: 3 conservative, "
                         "5 base, 9 optimistic)")
    ap.add_argument("--trades-per-day", type=float, default=2.0,
                    help="entries per session (replays observed 0-4)")
    ap.add_argument("--stop-pct", type=float, default=0.0017,
                    help="stop distance as a fraction of notional (0.5xATR on SPY ~ 0.0009)")
    ap.add_argument("--notional-pct", type=float, default=None,
                    help="notional per trade as a percent of equity (default: the config cap)")
    ap.add_argument("--vps-month", type=float, default=6.0)
    ap.add_argument("--api-month", type=float, default=0.30,
                    help="hosted model tokens; 0 if you run a local model")
    ap.add_argument("--gpu-month", type=float, default=0.0,
                    help="GPU hosting if you run a local 7B model on the server")
    args = ap.parse_args(argv)

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from llmtrader.config import Config

    cfg = Config.load()
    notional_pct = args.notional_pct if args.notional_pct is not None else cfg.max_notional_pct
    notional = args.equity * notional_pct / 100.0
    fixed = args.vps_month + args.api_month + args.gpu_month

    print(f"\neconomics: ${args.equity:,.0f} account | {args.edge_bps:.1f} bps edge per trade "
          f"| {args.trades_per_day:.1f} trades/day | {notional_pct:.0f}% notional per trade "
          f"(${notional:,.0f})")
    print(f"fixed cost: ${fixed:,.2f}/month "
          f"(vps ${args.vps_month:.2f} + tokens ${args.api_month:.2f} "
          f"+ gpu ${args.gpu_month:.2f})")

    print("\n  edge scenarios, net of the fixed cost")
    print(f"  {'edge':>8}  {'per trade':>10}  {'gross/mo':>10}  {'net/mo':>10}  {'net %/mo':>9}"
          f"  {'ann %':>7}")
    edges = sorted({1.0, 3.0, args.edge_bps, 9.0})
    for edge in edges:
        m = monthly(args.equity, notional, edge, args.trades_per_day)
        net = m["gross_month"] - fixed
        pct = net / args.equity * 100.0 if args.equity else 0.0
        print(f"  {edge:>7.1f}b  {m['per_trade']:>10.4f}  {m['gross_month']:>10.2f}"
              f"  {net:>10.2f}  {pct:>8.3f}%  {pct * 12:>6.1f}%")

    per_trade_sd, monthly_sd = noise(args.equity, notional, args.stop_pct, args.trades_per_day)
    print(f"\n  noise: each trade is about +/-${per_trade_sd:,.2f} "
          f"(stop {args.stop_pct * 100:.2f}% of ${notional:,.0f} notional)")
    print(f"  monthly P&L standard deviation about ${monthly_sd:,.2f} "
          f"({monthly_sd / args.equity * 100:.2f}% of equity)")

    m = monthly(args.equity, notional, args.edge_bps, args.trades_per_day)
    net_month = m["gross_month"] - fixed
    if net_month > 0:
        months = months_to_significance(net_month, monthly_sd)
        print(f"\n  expected edge: ${net_month:,.2f}/month after fixed costs")
        print(f"  to prove that edge is real rather than luck (t = 2) you would need about "
              f"{months:.0f} months")
        print(f"  i.e. roughly {months * 30:.0f} calendar days of running it, assuming the edge "
              f"does not decay")
    else:
        print(f"\n  expected edge: ${net_month:,.2f}/month after fixed costs - negative, "
              f"so there is nothing to prove")
        print("  the fixed cost of running it exceeds the measured edge at this account size")

    print(f"\n  what account size would cover a ${fixed:,.0f}/month fixed cost at "
          f"{args.edge_bps:.1f} bps and {args.trades_per_day:.1f} trades/day?")
    per_dollar_per_month = (
        (args.edge_bps / 10000.0) * args.trades_per_day * 21 * notional_pct / 100.0
    )
    if per_dollar_per_month > 0:
        breakeven = fixed / per_dollar_per_month
        print(f"    ${breakeven:,.0f}  (at {notional_pct:.0f}% notional per trade)")
    print("\n  Caveats that decide whether any of this is real:")
    print("    - The edge is a 60-session measurement of one instrument in one regime, with")
    print("      overlapping windows inflating its t-statistic by roughly three.")
    print("    - It assumes the process captures the edge. An LLM choosing entries, plus")
    print("      slippage and mistimed entries, is the largest unmodelled cost here.")
    print("    - Alpaca bracket orders need whole shares, so small accounts cannot hit a")
    print("      small notional target at all: 10% of $1,000 is 0.13 shares of SPY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
