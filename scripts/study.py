"""Signal study: do the scored dimensions predict anything?

No LLM involved. For every decision window in the lookback, compute exactly the context and scores
the bot sees, then measure what price did afterwards.

Two methodological points that decide whether the output means anything:

  * **Excess returns.** These sessions have drift. In a falling market every short-biased rule
    "works" and every long-biased rule "fails", purely from beta. So every bucket is reported
    against the unconditional mean of the same session. Conditional edge is the difference.
  * **Sample size.** 1-minute data only goes back about a week. 5-minute data goes back 60 days,
    and the same question can be asked at 5-minute resolution with 15m/1h derived by resampling,
    which is worth roughly twenty times the sample.

Overlapping windows still inflate significance. Demand a larger margin than the t-stat suggests.
"""

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.config import ROOT, Config
from llmtrader.context import build_context
from llmtrader.data.base import minutes_from_open, minutes_to_close, to_utc
from llmtrader.data.feeds import get_feed
from llmtrader.scorer import score_context

ROUND_TRIP_COST_BPS = 2.0
TAIL_BARS = 1500


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def t_stat(xs):
    xs = list(xs)
    if len(xs) < 3:
        return 0.0
    sd = statistics.stdev(xs)
    if sd == 0:
        return 0.0
    return mean(xs) / (sd / math.sqrt(len(xs)))


def timeframes_for(granularity):
    if granularity == "1m":
        return ("1m", "5m", "1h"), 1
    return ("5m", "15m", "1h"), 5


def collect(feed, cfg, symbol, horizons, granularity):
    tfs, interval = timeframes_for(granularity)
    period = "60d" if granularity == "5m" else "7d"
    bars = feed.bars(symbol, tf=granularity, limit=100000, cache_age_s=3600, period=period)
    if not bars:
        raise SystemExit(f"no {granularity} bars for {symbol}")
    bars.sort(key=lambda b: b.ts)
    sessions = sorted({b.et.date() for b in bars})
    print(f"  {len(bars)} {granularity} bars across {len(sessions)} sessions")

    rows = []
    for i, bar in enumerate(bars):
        now = to_utc(bar.ts)
        et = bar.et
        if et.minute % interval:
            continue
        try:
            if minutes_from_open(now) < cfg.no_entry_first_min:
                continue
            if minutes_to_close(now) < max(cfg.no_entry_last_min, max(horizons)):
                continue
        except Exception:
            continue
        window = bars[max(0, i - TAIL_BARS): i + 1]
        try:
            ctx = build_context(symbol, window, now, timeframes=tfs)
        except Exception:
            continue
        if ctx.session is None or not ctx.timeframes:
            continue
        c0 = ctx.timeframes.get(tfs[0])
        if c0 is None or c0.bars < 20:
            continue
        scored = score_context(ctx, getattr(ctx, "frames", None))
        price = ctx.price
        fwd = {}
        for h in horizons:
            target = now + timedelta(minutes=h)
            future = [b for b in bars if to_utc(b.ts) >= target]
            if not future:
                break
            fwd[h] = (future[0].close - price) / price * 10000.0
        if len(fwd) < len(horizons):
            continue
        rows.append({
            "ts": now.isoformat(),
            "day": str(et.date()),
            "price": price,
            "regime": ctx.regime,
            "trend": scored.trend,
            "trend_label": scored.trend_label,
            "momentum": scored.momentum,
            "momentum_label": scored.momentum_label,
            "mr": scored.mr_pressure,
            "mr_snap": scored.mr_snap,
            "volatility": scored.volatility,
            "volume": scored.volume_ratio,
            "volume_label": scored.volume_label,
            "adx5": c0.adx,
            "fwd": fwd,
        })

    baselines = {}
    for day in {r["day"] for r in rows}:
        day_rows = [r for r in rows if r["day"] == day]
        baselines[day] = {h: mean(r["fwd"][h] for r in day_rows) for h in horizons}
    for r in rows:
        base = baselines[r["day"]]
        r["excess"] = {h: r["fwd"][h] - base[h] for h in horizons}
    return rows, sessions, baselines


def bucket_report(rows, key, horizons, field="excess"):
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    out = []
    for name, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        entry = {"bucket": name, "n": len(group)}
        for h in horizons:
            vals = [r[field][h] for r in group if h in r[field]]
            if vals:
                entry[f"h{h}"] = round(mean(vals), 2)
                entry[f"t{h}"] = round(t_stat(vals), 2)
                entry[f"win{h}"] = round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1)
        out.append(entry)
    return out


def directional(rows, predicate, side, horizons, field="excess"):
    picked = [r for r in rows if predicate(r)]
    result = {"n": len(picked)}
    for h in horizons:
        vals = []
        for r in picked:
            if h not in r[field]:
                continue
            if side == "trend":
                sign = 1.0 if r["trend"] >= 0 else -1.0
            elif side == "fade":
                sign = -1.0 if r["trend"] >= 0 else 1.0
            else:
                sign = 1.0 if side == "long" else -1.0
            vals.append(sign * r[field][h])
        if vals:
            result[f"h{h}"] = round(mean(vals) - ROUND_TRIP_COST_BPS, 2)
            result[f"t{h}"] = round(t_stat(vals), 2)
            result[f"win{h}"] = round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1)
    return result


def print_table(title, rows, horizons):
    print(f"\n{title}")
    header = f"  {'bucket':<26} {'n':>5}"
    for h in horizons:
        header += f"  {f'h{h}':>8} {'t':>6} {'win%':>6}"
    print(header)
    for entry in rows:
        line = f"  {str(entry['bucket'])[:26]:<26} {entry['n']:>5}"
        for h in horizons:
            line += (
                f"  {entry.get(f'h{h}', 0):>8.2f} {entry.get(f't{h}', 0):>6.2f}"
                f" {entry.get(f'win{h}', 0):>6.1f}"
            )
        print(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure forward returns by scored dimension")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--granularity", default="5m", choices=["1m", "5m"])
    ap.add_argument("--horizons", default="15,30,60")
    ap.add_argument("--out", default=None)
    ap.add_argument("--compare", default=None,
                    help="comma separated symbols: rank them by measured edge instead of "
                         "studying one in depth")
    ap.add_argument("--write-priors", action="store_true",
                    help="write llmtrader/empirical_priors.json for the prompt to quote")
    args = ap.parse_args(argv)
    horizons = [int(h) for h in args.horizons.split(",")]
    cfg = Config.load()
    symbol = args.symbol.upper()
    cfg.symbols = [symbol]
    feed = get_feed("yfinance", frozen=True)

    if args.compare:
        from llmtrader.analysis import build_priors

        symbols = [s.strip().upper() for s in args.compare.split(",") if s.strip()]
        print(f"\ncomparing {len(symbols)} instruments | {args.granularity} resolution | "
              f"{horizons}m horizons | excess returns, net of {ROUND_TRIP_COST_BPS}bps")
        rows_out = []
        for sym in symbols:
            try:
                sym_rows, sym_sessions, _ = collect(
                    feed, cfg, sym, horizons, args.granularity
                )
            except SystemExit:
                print(f"  {sym:<9} no data, skipped")
                continue
            if len(sym_rows) < 200:
                print(f"  {sym:<9} only {len(sym_rows)} windows, skipped")
                continue
            priors = build_priors(sym_rows, horizons, sym, args.granularity)
            labels = priors.get("labels", {})
            bull = labels.get("BULL", {}).get("excess_bps")
            bear = labels.get("BEAR", {}).get("excess_bps")
            rules = {r["name"]: r for r in priors.get("rules", [])}
            fade = rules.get("fade adx>25 with volume>1.2", {})
            best = max(priors.get("rules", [{}]), key=lambda r: abs(r.get("excess_bps", 0)),
                       default={})
            rows_out.append({
                "symbol": sym, "windows": len(sym_rows), "sessions": len(sym_sessions),
                "bull": bull, "bear": bear,
                "fade": fade.get("excess_bps"), "fade_n": fade.get("n"),
                "best_name": best.get("name"), "best_bps": best.get("excess_bps"),
            })
            print(f"  {sym:<9} {len(sym_rows):>5} windows  "
                  f"BULL {bull if bull is not None else 0:+7.2f}  "
                  f"BEAR {bear if bear is not None else 0:+7.2f}  "
                  f"fade {fade.get('excess_bps', 0) or 0:+7.2f}  "
                  f"best {best.get('excess_bps', 0) or 0:+7.2f} ({best.get('name', '-')})")
        rows_out.sort(key=lambda r: -(abs(r.get("best_bps") or 0)))
        print("\n  ranked by the size of the largest single effect found:")
        for r in rows_out:
            print(f"    {r['symbol']:<9} {r['best_bps']:+7.2f} bps  "
                  f"n={r.get('best_n', '?')} {r['best_name']}")
        out = args.out or str(ROOT / "runs" / f"instrument-compare-{args.granularity}.json")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(rows_out, indent=2, default=str))
        print(f"\nwrote {out}")
        return 0
    print(f"\nsignal study: {symbol} | {args.granularity} resolution")
    rows, sessions, baselines = collect(feed, cfg, symbol, horizons, args.granularity)
    if not rows:
        raise SystemExit("no decision windows collected")
    print(f"  {len(rows)} decision windows | horizons {horizons}m "
          f"| cost assumption {ROUND_TRIP_COST_BPS} bps round trip")
    print("  every number below is EXCESS return: measured minus that session's "
          "unconditional mean at the same horizon")

    print("\nSession baselines to beat (raw bps, the drift these windows had to overcome)")
    print(f"  {'session':<14} {'windows':>8}" + "".join(f"  {f'h{h}bps':>9}" for h in horizons))
    for day in sorted(baselines):
        n = sum(1 for r in rows if r["day"] == day)
        cells = "".join(f"  {baselines[day][h]:>9.2f}" for h in horizons)
        print(f"  {day:<14} {n:>8}{cells}")

    print_table("Excess return by regime", bucket_report(rows, "regime", horizons), horizons)
    print_table("Excess return by trend label",
                bucket_report(rows, "trend_label", horizons), horizons)
    print_table("Excess return by momentum label",
                bucket_report(rows, "momentum_label", horizons), horizons)
    print_table("Excess return by volatility label",
                bucket_report(rows, "volatility", horizons), horizons)
    print_table("Excess return by volume label",
                bucket_report(rows, "volume_label", horizons), horizons)

    mr_buckets = []
    for lo, hi, name in ((0, 40, "MR 0-40 (calm)"), (40, 70, "MR 40-70 (stretched)"),
                         (70, 101, "MR 70-100 (extreme)")):
        group = [r for r in rows if lo <= r["mr"] < hi]
        entry = {"bucket": name, "n": len(group)}
        for h in horizons:
            vals = [r["excess"][h] for r in group if h in r["excess"]]
            if vals:
                entry[f"h{h}"] = round(mean(vals), 2)
                entry[f"t{h}"] = round(t_stat(vals), 2)
                entry[f"win{h}"] = round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1)
        mr_buckets.append(entry)
    print_table("Excess return by mean-reversion pressure", mr_buckets, horizons)

    print("\n=== OUT-OF-SAMPLE SPLIT: first half vs second half of the sessions ===")
    print("If an effect is real it should appear in both halves with the same sign.")
    days_sorted = sorted({r["day"] for r in rows})
    if len(days_sorted) >= 4:
        mid = len(days_sorted) // 2
        first_days, second_days = set(days_sorted[:mid]), set(days_sorted[mid:])
        halves = [
            ("H1 " + days_sorted[0] + ".." + days_sorted[mid - 1],
             [r for r in rows if r["day"] in first_days]),
            ("H2 " + days_sorted[mid] + ".." + days_sorted[-1],
             [r for r in rows if r["day"] in second_days]),
        ]
        key_rules = [
            ("BULL label (follow = wrong sign?)",
             lambda r: r["trend_label"] in ("BULL", "STRONG_BULL"), "long"),
            ("BEAR label (follow = wrong sign?)",
             lambda r: r["trend_label"] in ("BEAR", "STRONG_BEAR"), "short"),
            ("STRONG_UP momentum -> LONG",
             lambda r: r["momentum_label"] == "STRONG_UP", "long"),
            ("adx>25 & vol>1.2 -> FADE trend",
             lambda r: (r.get("adx5") or 0) > 25 and r["volume"] > 1.2, "fade"),
            ("MR>70 snap DOWN -> SHORT",
             lambda r: r["mr"] > 70 and r["mr_snap"] == "DOWN", "short"),
            ("volatility HIGH -> LONG",
             lambda r: r["volatility"] == "HIGH", "long"),
        ]
        print(f"\n  {'rule':<36}" + "".join(
            f"  {name:<22}" for name, _ in halves) + "  agreement")
        for name, predicate, side in key_rules:
            cells, signs = [], []
            for _label, subset in halves:
                res = directional(subset, predicate, side, horizons)
                v = res.get(f"h{horizons[-1]}", 0.0)
                t = res.get(f"t{horizons[-1]}", 0.0)
                cells.append(f"n={res['n']:<4} h{horizons[-1]}={v:+7.2f} t={t:+5.2f}")
                signs.append(1 if v > 0 else -1)
            agree = "SAME SIGN" if signs[0] == signs[1] else "disagrees"
            print(f"  {name:<36}" + "".join(f"  {c:<22}" for c in cells) + f"  {agree}")

    print("\nTradable rules: excess return in the direction traded, net of cost (bps)")
    rules = [
        ("fade extremes: MR>70 snap UP -> LONG",
         lambda r: r["mr"] > 70 and r["mr_snap"] == "UP", "long"),
        ("fade extremes: MR>70 snap DOWN -> SHORT",
         lambda r: r["mr"] > 70 and r["mr_snap"] == "DOWN", "short"),
        ("follow trend: BULL + mom UP -> LONG",
         lambda r: r["trend_label"] in ("BULL", "STRONG_BULL")
         and r["momentum_label"] in ("UP", "STRONG_UP"), "long"),
        ("follow trend: BEAR + mom DOWN -> SHORT",
         lambda r: r["trend_label"] in ("BEAR", "STRONG_BEAR")
         and r["momentum_label"] in ("DOWN", "STRONG_DOWN"), "short"),
        ("conflict: trend BEAR but MR>70 snap UP -> LONG",
         lambda r: r["trend_label"] in ("BEAR", "STRONG_BEAR")
         and r["mr"] > 70 and r["mr_snap"] == "UP", "long"),
        ("conflict: trend BULL but MR>70 snap DOWN -> SHORT",
         lambda r: r["trend_label"] in ("BULL", "STRONG_BULL")
         and r["mr"] > 70 and r["mr_snap"] == "DOWN", "short"),
        ("high adx>25 & volume>1.2 -> follow trend sign",
         lambda r: (r.get("adx5") or 0) > 25 and r["volume"] > 1.2, "trend"),
        ("high adx>25 & volume>1.2 -> FADE trend sign",
         lambda r: (r.get("adx5") or 0) > 25 and r["volume"] > 1.2, "fade"),
    ]
    print(f"  {'rule':<46} {'n':>5}" + "".join(f"  {f'h{h}':>8} {'t':>6}" for h in horizons))
    for name, predicate, side in rules:
        result = directional(rows, predicate, side, horizons)
        cells = "".join(
            f"  {result.get(f'h{h}', 0):>8.2f} {result.get(f't{h}', 0):>6.2f}" for h in horizons
        )
        print(f"  {name:<46} {result['n']:>5}{cells}")

    print("\nReading this: excess return strips out each session's drift, so a rule can no longer")
    print("look good merely for being short in a falling market. |t| < 2 is indistinguishable")
    print("from zero. Overlapping windows inflate t, so demand more than 2 here.")

    if args.write_priors:
        from llmtrader.analysis import build_priors
        from llmtrader.priors import DEFAULT_PATH

        priors = build_priors(rows, horizons, symbol, args.granularity)
        DEFAULT_PATH.write_text(json.dumps(priors, indent=2))
        print(f"\nwrote priors to {DEFAULT_PATH}")

    out = args.out or str(ROOT / "runs" / f"signal-study-{args.granularity}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "symbol": symbol,
        "granularity": args.granularity,
        "sessions": [str(s) for s in sessions],
        "windows": len(rows),
        "cost_bps": ROUND_TRIP_COST_BPS,
        "baselines": {k: v for k, v in baselines.items()},
        "by_regime": bucket_report(rows, "regime", horizons),
        "by_trend": bucket_report(rows, "trend_label", horizons),
        "by_momentum": bucket_report(rows, "momentum_label", horizons),
        "by_volatility": bucket_report(rows, "volatility", horizons),
        "by_volume": bucket_report(rows, "volume_label", horizons),
        "by_mr": mr_buckets,
    }, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
