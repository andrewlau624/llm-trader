
from . import risk
from .broker.base import BrokerError
from .config import Config
from .context import build_context
from .data.base import ET, to_utc
from .feedback import trade_feedback
from .gate import should_call_llm
from .news import build_news
from .scorer import score_context
from .trader import Decision, Trader


def cross_market(feed, cfg, now, tf="5m", bars=15, warnings=None, cache_age_s=900):
    out = {}
    for sym in cfg.extra_context_symbols:
        try:
            series = feed.bars(sym, tf=tf, limit=bars + 40, cache_age_s=cache_age_s)
        except Exception as e:
            if warnings is not None:
                warnings.append(f"{sym} data unavailable ({type(e).__name__})")
            continue
        usable = [b for b in series if to_utc(b.ts) < to_utc(now)]
        if len(usable) < 3:
            if warnings is not None:
                warnings.append(f"{sym} data incomplete ({len(usable)} bars before now)")
            continue
        window = usable[-bars:]
        first = window[0].close
        if not first:
            if warnings is not None:
                warnings.append(f"{sym} has a zero close in the window")
            continue
        out[f"{sym} {tf}"] = round((window[-1].close - first) / first * 100.0, 2)
    return out


def recent_lines(decisions, trades, limit=8):
    items = []
    for d in decisions[-limit:]:
        dec = d["decision"]
        t = to_utc_stamp(d["ts"])
        stamp = f"{t.astimezone(ET):%H:%M}" if t else "????"
        txt = f"{stamp} {dec['action']} conf {dec['confidence']}"
        if dec["action"] != "hold":
            txt += (
                f" entry {dec.get('entry')} stop {dec.get('stop_loss')}"
                f" tp {dec.get('take_profit')}"
            )
            if d["risk"]["allowed"]:
                txt += f" -> APPROVED size {d['risk']['size']:.2f}"
            else:
                txt += f" -> REJECTED ({'; '.join(d['risk']['reasons'])})"
        elif not dec.get("parse_ok", True):
            txt += " -> PARSE FAILURE"
        items.append(txt)
    for tr in trades[-limit:]:
        t = to_utc_stamp(tr.get("closed_at"))
        stamp = f"{t.astimezone(ET):%H:%M}" if t else "????"
        items.append(
            f"{stamp} CLOSED {tr['side']} {tr['exit_reason']} "
            f"pnl {tr['pnl']:+,.0f} ({tr['r_multiple']:+.2f}R) held {tr['hold_min']}m"
        )
    return items[-limit:]


def to_utc_stamp(ts):
    from datetime import datetime

    if not ts:
        return None
    if isinstance(ts, str):
        try:
            return to_utc(datetime.fromisoformat(ts))
        except ValueError:
            return None
    return to_utc(ts)


class Engine:
    def __init__(self, cfg=None, feed=None, client=None, broker=None, journal=None, logger=print):
        self.cfg = cfg or Config()
        self.feed = feed
        self.client = client
        self.broker = broker
        self.journal = journal
        self.logger = logger or (lambda *a, **k: None)
        self.trader = Trader(client, self.cfg) if client is not None else None
        self.history_days = 3
        self.news = build_news(self.cfg)

    def market_context(self, now, prebuilt=None):
        symbol = self.cfg.symbols[0]
        warnings = []
        bars = self.feed.bars_1m(symbol, days=self.history_days)
        if not bars:
            raise RuntimeError(f"no bars returned for {symbol}")
        cross = cross_market(self.feed, self.cfg, now, warnings=warnings)
        for tf in self.cfg.timeframes:
            if tf != "1m" and prebuilt and not prebuilt.get(tf):
                warnings.append(f"{tf} history missing, {tf} context derived from 1m bars only")
        ctx = build_context(
            symbol,
            bars,
            now,
            timeframes=tuple(self.cfg.timeframes),
            prebuilt=prebuilt,
            extra=cross,
        )
        ctx.warnings = warnings
        if warnings:
            ctx.regime_notes = list(ctx.regime_notes) + [f"DATA WARNING: {w}" for w in warnings]
        return ctx

    def step(self, now, prebuilt=None, account=None, decisions=None, trades=None):
        ctx = self.market_context(now, prebuilt)
        decisions = decisions if decisions is not None else self.journal.decisions()
        trades = trades if trades is not None else self.journal.trades()
        if account is None:
            account = (
                self.broker.account_state(ctx.price)
                if hasattr(self.broker, "account_state")
                else self.broker.snapshot(ctx.price)
            )
        risk.update_halt(account, self.cfg)
        recent = recent_lines(decisions, trades)

        if account.halted and not self.cfg.call_llm_when_halted:
            skipped = Decision(action="hold", thesis="account halted")
            checks = risk.check(skipped, ctx, account, self.cfg)
            self.journal.log_decision(
                ctx.now, ctx, "", skipped, checks, account, extra={"skipped": "halted"}
            )
            return {"ctx": ctx, "decision": skipped, "checks": checks, "account": account}

        if account.position is not None and not self.cfg.call_llm_in_position:
            skipped = Decision(
                action="hold", thesis="position already open, exits are rule-managed"
            )
            checks = risk.check(skipped, ctx, account, self.cfg)
            self.journal.log_decision(
                ctx.now, ctx, "", skipped, checks, account, extra={"skipped": "in_position"}
            )
            return {"ctx": ctx, "decision": skipped, "checks": checks, "account": account}

        call, gate_notes = should_call_llm(ctx, self.cfg)
        if not call:
            skipped = Decision(action="hold", thesis="; ".join(gate_notes))
            checks = risk.check(skipped, ctx, account, self.cfg)
            self.journal.log_decision(
                ctx.now, ctx, "", skipped, checks, account,
                extra={"skipped": "gate", "gate": gate_notes},
            )
            return {"ctx": ctx, "decision": skipped, "checks": checks, "account": account}

        news_notes = self.news.notes(ctx.now) if self.news else []
        scored = score_context(ctx, getattr(ctx, "frames", None))
        ctx.scored = scored
        feedback = trade_feedback(trades, day=ctx.now.astimezone(ET).date())
        decision, dashboard = self.trader.decide(
            ctx, account=account, recent=recent, extra=news_notes or None,
            scored=scored, feedback=feedback,
        )
        decision.regime = ctx.regime
        checks = risk.check(decision, ctx, account, self.cfg)
        self.journal.log_decision(
            ctx.now, ctx, dashboard, decision, checks, account, extra={"gate": gate_notes}
        )
        if checks.allowed:
            try:
                self.broker.submit(decision, checks.size, ctx.now)
            except BrokerError as e:
                self.journal.log_event(
                    {"event": "entry_not_placed", "error": str(e)[:300],
                     "ts": ctx.now.isoformat(), "action": decision.action}
                )
                self.logger(f"  ENTRY NOT PLACED: {e}")
                return {
                    "ctx": ctx, "decision": decision, "checks": checks,
                    "account": account, "error": str(e),
                }
            self.logger(
                f"  ENTRY {decision.action} size {checks.size:.2f} risk ${checks.risk_dollars:.0f} "
                f"entry {decision.entry} stop {decision.stop_loss} tp {decision.take_profit}"
            )
        elif decision.action != "hold":
            self.logger(f"  rejected: {checks.summary()}")
        return {"ctx": ctx, "decision": decision, "checks": checks, "account": account}
