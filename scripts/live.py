import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.config import Config
from llmtrader.data.base import ET, RTH_CLOSE, RTH_OPEN, to_utc
from llmtrader.data.feeds import get_feed
from llmtrader.journal import Journal
from llmtrader.llm import build_client
from llmtrader.report import render_report
from llmtrader.runner import Engine


def next_decision_time(now_utc, interval):
    et = to_utc(now_utc).astimezone(ET)
    base = et.replace(second=0, microsecond=0)
    remainder = base.minute % interval
    if remainder == 0 and et.second == 0:
        candidate = base
    else:
        candidate = base + timedelta(minutes=(interval - remainder) % interval)
    while candidate <= et:
        candidate += timedelta(minutes=interval)
    return candidate.astimezone(timezone.utc)


def market_open(now_utc):
    et = to_utc(now_utc).astimezone(ET)
    if et.weekday() >= 5:
        return False
    return RTH_OPEN <= et.time() < RTH_CLOSE


def sleep_until_open(now_utc):
    et = to_utc(now_utc).astimezone(ET)
    if et.time() >= RTH_CLOSE or et.weekday() >= 5:
        days = 1
        nxt = et + timedelta(days=days)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        nxt = nxt.replace(hour=RTH_OPEN.hour, minute=RTH_OPEN.minute, second=0, microsecond=0)
    else:
        nxt = et.replace(hour=RTH_OPEN.hour, minute=RTH_OPEN.minute, second=0, microsecond=0)
    return max(30.0, (nxt - et).total_seconds())


class SimPump:
    def __init__(self, feed, broker, journal, symbol, logger=print):
        self.feed = feed
        self.broker = broker
        self.journal = journal
        self.symbol = symbol
        self.logger = logger
        self.last_ts = None
        self.last_price = None

    def prime(self):
        now = datetime.now(timezone.utc)
        bars = self.feed.bars_1m(self.symbol, days=3)
        done = [b for b in bars if to_utc(b.ts) + timedelta(minutes=1) <= now]
        if done:
            self.last_ts = done[-1].ts
            self.last_price = done[-1].close

    def advance(self):
        bars = self.feed.bars_1m(self.symbol, days=3)
        if not bars:
            return
        if self.last_ts is None:
            self.last_ts = max(b.ts for b in bars)
            return
        fresh = [b for b in bars if to_utc(b.ts) > to_utc(self.last_ts)]
        if not fresh:
            return
        completed = [
            b for b in fresh if to_utc(b.ts) + timedelta(minutes=1) <= datetime.now(timezone.utc)
        ]
        if not completed:
            return
        before = len(self.broker.trades)
        for b in completed:
            self.broker.process_bar(b)
            self.last_ts = b.ts
            self.last_price = b.close
        for t in self.broker.trades[before:]:
            self.journal.log_trade(t)
            self.logger(
                f"  closed {t.side} {t.exit_reason} pnl {t.pnl:+.2f} ({t.r_multiple:+.2f}R)"
            )


def build_broker(args, cfg):
    if args.broker == "alpaca":
        from llmtrader.broker.alpaca import AlpacaBroker

        return AlpacaBroker(cfg)
    from llmtrader.broker import LocalAccount
    from llmtrader.broker.sim import SimBroker

    return SimBroker(cfg, account=LocalAccount(cfg.equity))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Live paper trading loop (Alpaca paper or simulated fills)"
    )
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--broker", default="sim", choices=["sim", "alpaca"])
    ap.add_argument("--source", default=None, choices=["yfinance", "alpaca"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default=None, choices=["ollama", "deepseek", "opencode-go"])
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--once", action="store_true", help="run one decision cycle and exit")
    ap.add_argument("--force", action="store_true", help="ignore the market-hours check")
    ap.add_argument(
        "--gate", action="store_true",
        help="only call the LLM when the context is actionable",
    )
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
    if args.source:
        cfg.data_source = args.source

    symbol = cfg.symbols[0]
    feed = get_feed(cfg.data_source, frozen=False)
    client = build_client(cfg)
    journal = Journal(cfg, symbol=symbol, mode="paper")
    broker = build_broker(args, cfg)
    engine = Engine(cfg, feed=feed, client=client, broker=broker, journal=journal)
    pump = SimPump(feed, broker, journal, symbol) if args.broker == "sim" else None
    journal.write_meta(
        {
            "mode": "paper",
            "broker": args.broker,
            "source": cfg.data_source,
            "symbol": symbol,
            "model": cfg.ollama_model,
            "backend": cfg.llm_backend,
            "interval_min": cfg.decision_interval_min,
            "gate": cfg.llm_gate,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "config": {k: v for k, v in cfg.__dict__.items()},
        }
    )
    print(
        f"paper run: {symbol} on {args.broker} ({cfg.data_source} data) | "
        f"{cfg.ollama_model} via {cfg.llm_backend} | interval {cfg.decision_interval_min}m"
    )
    print(f"journal: {journal.dir}")
    if args.broker == "sim":
        print("simulated fills: positions are managed bar by bar from live data")
    if pump:
        pump.prime()

    while True:
        now = datetime.now(timezone.utc)
        if not market_open(now) and not args.force:
            et = now.astimezone(ET)
            sleep_s = sleep_until_open(now)
            print(f"{et:%H:%M} ET market closed, sleeping {sleep_s / 60:.0f}m")
            if args.once:
                return 0
            time.sleep(min(sleep_s, 900))
            continue

        try:
            if args.broker == "alpaca":
                for t in broker.sync(now):
                    journal.log_trade(t)
                    print(
                        f"  closed {t.side} {t.exit_reason} pnl {t.pnl:+.2f} "
                        f"({t.r_multiple:+.2f}R)"
                    )
            if pump:
                pump.advance()
            if args.broker == "alpaca" and broker.open_entry and not market_open(now):
                print("  market closed with an unfilled entry: cancelling so it cannot fill "
                      "at the next open")
                broker.cancel_open_orders()
            price = pump.last_price if pump else None
            account = (
                broker.account_state(price)
                if args.broker == "alpaca"
                else broker.snapshot(price)
            )
            if args.broker == "alpaca" and broker.forced_exit_due(now, price):
                print("  forced exit: position held past its time limit")
                broker.close_all()
            engine.step(now, account=account)
        except Exception as e:
            traceback.print_exc()
            journal.log_event({"event": "error", "error": str(e), "ts": now.isoformat()})

        if args.once:
            break
        nxt = next_decision_time(now, cfg.decision_interval_min)
        sleep_s = (nxt - datetime.now(timezone.utc)).total_seconds()
        time.sleep(max(5.0, sleep_s))

    print()
    print(render_report(journal.decisions(), journal.trades(), title=journal.dir.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
