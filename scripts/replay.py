import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.broker import LocalAccount
from llmtrader.broker.sim import SimBroker
from llmtrader.config import Config
from llmtrader.data.base import ET, minutes_from_open, minutes_to_close, to_utc
from llmtrader.data.feeds import get_feed
from llmtrader.journal import Journal
from llmtrader.llm import build_client
from llmtrader.report import render_report
from llmtrader.runner import Engine


def decision_points(bars, cfg):
    interval = cfg.decision_interval_min
    days = sorted({b.et.date() for b in bars})
    points = []
    for day in days:
        day_bars = [b for b in bars if b.et.date() == day]
        if len(day_bars) < 60:
            continue
        last = day_bars[-1].ts
        t = day_bars[0].ts
        while t <= last:
            m_from_open = minutes_from_open(t)
            m_to_close = minutes_to_close(t)
            if (
                m_from_open >= cfg.no_entry_first_min
                and m_to_close >= cfg.no_entry_last_min
                and to_utc(t).astimezone(ET).minute % interval == 0
            ):
                points.append(t)
            t = t + timedelta(minutes=1)
    return sorted(points)


def prebuilt_frames(feed, symbol, points, cfg):
    frames = {}
    for tf in cfg.timeframes:
        if tf == "1m":
            continue
        try:
            series = feed.bars(symbol, tf=tf, limit=400, cache_age_s=3600)
        except Exception as e:
            print(f"  could not load {tf} history: {e}")
            continue
        if series:
            frames[tf] = series
    return frames


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay the LLM trader over recent 1m bars")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--days", type=int, default=2, help="days of 1m history (max 7)")
    ap.add_argument("--date", default=None, help="single session YYYY-MM-DD")
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None, choices=["ollama", "deepseek", "opencode-go"])
    ap.add_argument("--interval", type=int, default=None, help="decision interval minutes")
    ap.add_argument("--max-decisions", type=int, default=0)
    ap.add_argument("--gate", action="store_true",
                    help="only call the LLM when the context looks actionable")
    ap.add_argument("--tag", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config.load()
    if args.symbol:
        cfg.symbols = [args.symbol.upper()]
    if args.model:
        cfg.ollama_model = args.model
    if args.backend:
        cfg.llm_backend = args.backend
    if args.interval:
        cfg.decision_interval_min = args.interval
    if args.gate:
        cfg.llm_gate = True

    symbol = cfg.symbols[0]
    print(
        f"replay: {symbol} | model {cfg.ollama_model} via {cfg.llm_backend} | "
        f"interval {cfg.decision_interval_min}m | days {args.days}"
    )

    history_days = max(3, args.days)
    feed = get_feed("yfinance", frozen=True)
    bars = feed.bars_1m(symbol, days=history_days, cache_age_s=1800)
    if not bars:
        print("no data returned, aborting")
        return 1
    bars.sort(key=lambda b: b.ts)
    if args.date:
        target = date.fromisoformat(args.date)
        if not [b for b in bars if b.et.date() == target]:
            print(f"no bars for {args.date} in the retrievable window")
            return 1
    decision_bars = bars if not args.date else [b for b in bars if b.et.date() == target]
    print(
        f"  {len(bars)} 1m bars from {bars[0].et:%Y-%m-%d %H:%M} "
        f"to {bars[-1].et:%Y-%m-%d %H:%M} ET"
    )

    frames = prebuilt_frames(feed, symbol, [], cfg)
    points = decision_points(decision_bars, cfg)
    if args.max_decisions:
        points = points[: args.max_decisions]
    print(f"  {len(points)} decision windows")

    client = build_client(cfg)
    journal = Journal(cfg, symbol=symbol, mode="replay")
    broker = SimBroker(cfg, account=LocalAccount(cfg.equity))
    engine = Engine(cfg, feed=feed, client=client, broker=broker,
                    journal=journal, logger=None if args.quiet else print)
    engine.history_days = history_days
    journal.write_meta(
        {
            "mode": "replay",
            "symbol": symbol,
            "model": cfg.ollama_model,
            "backend": cfg.llm_backend,
            "interval_min": cfg.decision_interval_min,
            "days": args.days,
            "date": args.date,
            "tag": args.tag,
            "bar_count": len(bars),
            "decision_windows": len(points),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "config": {k: v for k, v in cfg.__dict__.items()},
        }
    )

    cursor = 0
    logged_trades = 0
    t0 = time.time()
    for i, point in enumerate(points, 1):
        engine.step(point, prebuilt=frames)
        while cursor < len(bars) and to_utc(bars[cursor].ts) < to_utc(point) + timedelta(
            minutes=cfg.decision_interval_min
        ):
            broker.process_bar(bars[cursor])
            cursor += 1
        while logged_trades < len(broker.trades):
            journal.log_trade(broker.trades[logged_trades])
            logged_trades += 1
        if not args.quiet and i % 10 == 0:
            acct = broker.snapshot(bars[cursor - 1].close if cursor else None)
            print(
                f"  [{i}/{len(points)}] equity {acct.equity:,.0f} day pnl {acct.day_pnl:+,.0f} "
                f"trades {acct.trades_today} elapsed {time.time() - t0:.0f}s"
            )

    while cursor < len(bars):
        broker.process_bar(bars[cursor])
        cursor += 1
    while logged_trades < len(broker.trades):
        journal.log_trade(broker.trades[logged_trades])
        logged_trades += 1
    if broker.account.position is not None:
        forced = broker.force_close(bars[-1].close, bars[-1].ts, reason="replay_end")
        if forced:
            journal.log_trade(forced)
    for ev in broker.events:
        journal.log_event(ev)

    decisions = journal.decisions()
    trades = journal.trades()
    print()
    print(render_report(decisions, trades, title=journal.dir.name))
    print()
    print(f"journal: {journal.dir}")
    print(f"elapsed: {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
