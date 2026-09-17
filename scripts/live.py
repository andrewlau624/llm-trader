import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.broker.base import BrokerError
from llmtrader.config import Config
from llmtrader.data.base import ET, RTH_CLOSE, RTH_OPEN, to_utc
from llmtrader.data.feeds import get_feed
from llmtrader.journal import Journal
from llmtrader.llm import build_client
from llmtrader.priors import load_priors
from llmtrader.report import render_report
from llmtrader.risk import check
from llmtrader.runner import Engine
from llmtrader.strategy import candidates_for
from llmtrader.trader import Decision


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
    ap.add_argument("--notional-pct", type=float, default=None,
                    help="notional per trade as a percent of equity. The shipped 10%% is the "
                         "bug this repo found: a 1-ATR stop wants ~147%%, so the cap binds and "
                         "every trade runs at 0.04%% risk instead of the configured 0.25%%.")
    ap.add_argument("--once", action="store_true", help="run one decision cycle and exit")
    ap.add_argument("--force", action="store_true", help="ignore the market-hours check")
    ap.add_argument(
        "--gate", action="store_true",
        help="only call the LLM when the context is actionable",
    )
    ap.add_argument("--strategy", default="llm", choices=["llm", "deterministic"],
                    help="deterministic runs the measured reversal rule with no model in the "
                         "loop; the backtest says the formula works and the model does not")
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
    if args.notional_pct is not None:
        cfg.max_notional_pct = args.notional_pct
    if args.gate:
        cfg.llm_gate = True
    if args.source:
        cfg.data_source = args.source

    symbol = cfg.symbols[0]
    basket = list(dict.fromkeys(cfg.basket or cfg.symbols))
    deterministic = args.strategy == "deterministic"
    feed = get_feed(cfg.data_source, frozen=False)
    client = None if deterministic else build_client(cfg)
    journal = Journal(cfg, symbol=symbol, mode="paper")
    broker = build_broker(args, cfg)
    deterministic = args.strategy == "deterministic"
    engine = None
    priors = load_priors() if deterministic else None
    if deterministic:
        cfg.symbols = basket
        if not priors:
            print("no priors file: run 'make study' first so the rule can price its own setups")
    else:
        cfg.symbols = [symbol]
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
    if deterministic:
        print(
            f"paper run: {args.broker} ({cfg.data_source} data) | deterministic reversal rule | "
            f"interval {cfg.decision_interval_min}m | basket {', '.join(basket)}"
        )
    else:
        print(
            f"paper run: {symbol} on {args.broker} ({cfg.data_source} data) | "
            f"{cfg.ollama_model} via {cfg.llm_backend} | interval {cfg.decision_interval_min}m"
        )
    print(f"journal: {journal.dir}")
    print(f"strategy: {'deterministic reversal rule (no LLM)' if deterministic else 'LLM'}"
          f"{' | basket ' + ', '.join(basket) if deterministic else ''}")
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
            if args.broker == "alpaca":
                for rec in broker.measure_slippage():
                    journal.log_event(rec)
                    print(
                        f"  FILL-VS-SIGNAL {rec['symbol']}: signal {rec['signal_price']} -> fill "
                        f"{rec['fill_price']} = {rec['cost_bps']:+.2f} bps "
                        f"(model assumes {rec['modelled_bps']} bps, edge "
                        f"{rec.get('expected_bps')} bps)"
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
            if deterministic:
                bars_by_symbol = {}
                for sym in basket:
                    try:
                        series = feed.bars_1m(sym, days=3)
                    except Exception:
                        series = []
                    if series:
                        bars_by_symbol[sym] = series
                picks = candidates_for(
                    bars_by_symbol, now, cfg, priors, tuple(cfg.timeframes)
                )
                if not picks:
                    journal.log_event(
                        {"ts": now.isoformat(), "event": "no_candidate",
                         "symbols": len(bars_by_symbol)}
                    )
                else:
                    picks.sort(key=lambda pair: -(pair[0].expected_bps * pair[0].score))
                    cand, ctx = picks[0]
                    acct = (
                        broker.account_state(ctx.price) if args.broker == "alpaca"
                        else broker.snapshot(ctx.price)
                    )
                    decision = Decision(
                        action="enter_long" if cand.direction == "long" else "enter_short",
                        confidence=min(10, max(1, int(cand.score / 10))),
                        entry=cand.entry, stop_loss=cand.stop, take_profit=cand.take_profit,
                        size_multiplier=1.0, max_hold_minutes=cfg.max_hold_min,
                        thesis=cand.reason,
                        invalidation="price keeps going instead of snapping back",
                    )
                    decision.regime = ctx.regime
                    decision.symbol = cand.symbol
                    decision.expected_bps = cand.expected_bps
                    verdict = check(decision, ctx, acct, cfg)
                    journal.log_decision(
                        now, ctx, "", decision, verdict, acct,
                        extra={"strategy": "deterministic",
                               "expected_bps": cand.expected_bps,
                               "signal_price": cand.entry,
                               "candidates": len(picks)},
                    )
                    if verdict.allowed:
                        try:
                            broker.submit(decision, verdict.size, now)
                        except BrokerError as e:
                            journal.log_event({
                                "event": "entry_not_placed", "error": str(e)[:300],
                                "ts": now.isoformat(), "symbol": cand.symbol,
                            })
                            print(f"  NOT PLACED {cand.symbol}: {str(e)[:140]}")
                        else:
                            print(f"  ENTRY {decision.action} {cand.symbol} "
                                  f"size {verdict.size:.2f} expected "
                                  f"{cand.expected_bps:.1f}bps | {cand.reason[:80]}")
                    else:
                        print(f"  signal {cand.symbol} rejected: {verdict.summary()[:120]}")
            else:
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
