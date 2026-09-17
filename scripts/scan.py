"""Deterministic scanner: run the measured reversal rule across a basket.

No LLM. It answers one question honestly: does the formula the study measured actually produce
trades when applied mechanically, and what would they return under the same risk rules the LLM
path uses?

    python scripts/scan.py --symbols SPY,QQQ,IWM --granularity 5m

One position at a time across the whole basket, because the account object holds a single
position. That makes this the conservative sequential reading of the rule rather than a fully
diversified portfolio of it.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.broker import LocalAccount
from llmtrader.broker.sim import SimBroker
from llmtrader.config import ROOT, Config
from llmtrader.context import build_context
from llmtrader.data.base import minutes_from_open, minutes_to_close, to_utc
from llmtrader.data.feeds import get_feed
from llmtrader.priors import load_priors
from llmtrader.reversal import evaluate
from llmtrader.risk import check
from llmtrader.scorer import score_context
from llmtrader.trader import Decision

TAIL = 1500


def timeframes_for(granularity):
    return ("5m", "15m", "1h") if granularity == "5m" else ("1m", "5m", "1h")


def load_bars(feed, symbols, granularity):
    out = {}
    period = "60d" if granularity == "5m" else "7d"
    for sym in symbols:
        try:
            bars = feed.bars(sym, tf=granularity, limit=100000, cache_age_s=3600, period=period)
        except Exception:
            bars = []
        if bars:
            out[sym] = sorted(bars, key=lambda b: b.ts)
        else:
            print(f"  {sym}: no data, skipped")
    return out


def simulate(feed, cfg, symbols, granularity, priors, interval=5, control=None):
    bars_by_symbol = load_bars(feed, symbols, granularity)
    if not bars_by_symbol:
        raise SystemExit("no bars for any symbol")
    tfs = timeframes_for(granularity)
    account = LocalAccount(cfg.equity)
    brokers = {sym: SimBroker(cfg, account=account) for sym in bars_by_symbol}
    cursors = {sym: 0 for sym in bars_by_symbol}
    tracked = {sym: 0 for sym in bars_by_symbol}
    trades = []
    candidates = 0

    reference = bars_by_symbol[next(iter(bars_by_symbol))]
    sessions = sorted({b.et.date() for b in reference})
    tz = reference[0].et.tzinfo

    for day in sessions:
        if not [b for b in reference if b.et.date() == day]:
            continue
        account.start_day(day, account.equity)
        stamps = sorted({b.ts for b in reference if b.et.date() == day})
        for ts in stamps:
            now = to_utc(ts)
            emitted = []
            for sym, bars in bars_by_symbol.items():
                broker = brokers[sym]
                while cursors[sym] < len(bars) and to_utc(bars[cursors[sym]].ts) <= now:
                    broker.process_bar(bars[cursors[sym]])
                    cursors[sym] += 1
                new = broker.trades[tracked[sym]:]
                tracked[sym] = len(broker.trades)
                emitted.extend(new)
            trades.extend(emitted)

            et = now.astimezone(tz)
            if et.minute % interval:
                continue
            if account.position is not None:
                continue
            if minutes_from_open(now) < cfg.no_entry_first_min:
                continue
            if minutes_to_close(now) < cfg.no_entry_last_min + 5:
                continue

            picks = []
            for sym, bars in bars_by_symbol.items():
                upto = [b for b in bars if to_utc(b.ts) <= now]
                window = upto[-TAIL:]
                if len(window) < 60:
                    continue
                try:
                    ctx = build_context(sym, window, now, timeframes=tfs)
                except Exception:
                    continue
                if ctx.session is None or ctx.price is None:
                    continue
                scored = score_context(ctx, getattr(ctx, "frames", None))
                cand = evaluate(ctx, scored, priors=priors, cfg=cfg)
                if cand:
                    picks.append((cand, ctx))
            candidates += len(picks)
            if not picks:
                continue
            picks.sort(key=lambda p: -(p[0].expected_bps * p[0].score))
            cand, ctx = picks[0]
            if control == "flip":
                cand = replace(cand, direction="short" if cand.direction == "long" else "long",
                               reason="CONTROL: direction inverted")
                atr = abs(cand.entry - cand.stop)
                if cand.direction == "long":
                    cand = replace(cand, stop=cand.entry - atr, take_profit=cand.entry + 2 * atr)
                else:
                    cand = replace(cand, stop=cand.entry + atr, take_profit=cand.entry - 2 * atr)
            elif control == "random":
                import random as _random

                side = _random.choice(["long", "short"])
                atr = abs(cand.entry - cand.stop)
                cand = replace(
                    cand, direction=side, reason="CONTROL: random direction",
                    stop=cand.entry + (atr if side == "short" else -atr),
                    take_profit=cand.entry + (-2 * atr if side == "short" else 2 * atr),
                )
            decision = Decision(
                action="enter_long" if cand.direction == "long" else "enter_short",
                confidence=min(10, max(1, int(cand.score / 10))),
                entry=cand.entry,
                stop_loss=cand.stop,
                take_profit=cand.take_profit,
                size_multiplier=1.0,
                max_hold_minutes=cfg.max_hold_min,
                thesis=cand.reason,
                invalidation="price keeps going instead of snapping back to the mean",
            )
            decision.regime = ctx.regime
            verdict = check(decision, ctx, account.snapshot(ctx.price), cfg)
            if not verdict.allowed:
                continue
            brokers[cand.symbol].submit(decision, verdict.size, now)
    return trades, candidates, sessions


def report(trades, candidates, sessions, symbols, equity, configured_risk_pct=0.25):
    print(f"\n  sessions {len(sessions)} | symbols {len(symbols)} | candidate signals across the "
          f"basket {candidates} | trades taken {len(trades)}")
    if not trades:
        print("  no trades were taken")
        return
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    net = sum(pnls)
    curve = peak = dd = 0.0
    for p in pnls:
        curve += p
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    losses = len(trades) - len(wins)
    pf = (sum(wins) / gross_loss) if gross_loss else float("inf")
    print(f"  win rate {len(wins) / len(trades) * 100:.1f}% ({len(wins)}W/{losses}L)  "
          f"net {net:+,.2f} ({net / equity * 100:+.2f}% of equity)")
    print(f"  max drawdown {dd:,.2f}  profit factor {pf:.2f}")
    risks = [abs(t.entry - t.stop) * t.qty for t in trades]
    notionals = [abs(t.entry * t.qty) for t in trades]
    print(f"  avg {statistics.mean(pnls):+,.2f}/trade  avg R "
          f"{statistics.mean([t.r_multiple for t in trades]):+.3f}")
    print(f"  effective risk/trade ${statistics.mean(risks):,.2f} "
          f"({statistics.mean(risks) / equity * 100:.3f}% of equity, configured "
          f"{configured_risk_pct}%) | notional ${statistics.mean(notionals):,.0f}")
    by_sym = defaultdict(lambda: [0, 0.0])
    for t in trades:
        by_sym[t.symbol][0] += 1
        by_sym[t.symbol][1] += t.pnl
    print("  by symbol: " + " | ".join(
        f"{s} {n}x {p:+,.0f}" for s, (n, p) in sorted(by_sym.items())))
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason] += 1
    print(f"  exits: {dict(reasons)}")
    holds = [t.hold_min for t in trades]
    print(f"  hold: avg {statistics.mean(holds):.0f}m  max {max(holds)}m")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic reversal scanner")
    ap.add_argument("--symbols", default="SPY,QQQ,IWM")
    ap.add_argument("--granularity", default="5m", choices=["1m", "5m"])
    ap.add_argument("--equity", type=float, default=None)
    ap.add_argument("--control", default=None, choices=["flip", "random"],
                    help="sanity check: same mechanics, direction flipped or randomised. "
                         "If a control also makes money, the simulation is flattering itself.")
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cfg = Config.load()
    if args.equity:
        cfg.equity = args.equity
    priors = load_priors()
    feed = get_feed("yfinance", frozen=True)
    print(f"\ndeterministic scan: {', '.join(symbols)} | {args.granularity} | equity "
          f"${cfg.equity:,.0f} | priors {'loaded' if priors else 'MISSING (run make study)'}"
          f"{' | CONTROL=' + args.control if args.control else ''}")
    trades, candidates, sessions = simulate(
        feed, cfg, symbols, args.granularity, priors, control=args.control
    )
    report(trades, candidates, sessions, symbols, cfg.equity, cfg.risk_per_trade_pct)
    out = ROOT / "runs" / "scan-trades.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([t.to_dict() for t in trades], indent=2, default=str))
    print(f"\n  wrote {out}")
    print("\n  remember: one position at a time across the basket, so this understates how many")
    print("  signals the rule actually produced, and overstates how correlated they were")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
