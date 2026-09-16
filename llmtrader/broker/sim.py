from ..data.base import minutes_to_close, to_utc
from ..risk import update_halt
from .accounting import LocalAccount
from .base import Position, Trade


class SimBroker:
    name = "sim"

    def __init__(self, cfg, account=None):
        self.cfg = cfg
        self.account = account or LocalAccount(cfg.equity)
        self.pending = None
        self.trades = []
        self.events = []

    def submit(self, decision, size, now):
        self.pending = {"decision": decision, "size": size, "ts": to_utc(now)}

    def cancel_pending(self):
        self.pending = None

    def _slip(self, price, direction):
        factor = self.cfg.slippage_bps / 10000.0
        return price * (1 + factor) if direction > 0 else price * (1 - factor)

    def process_bar(self, bar):
        bar_ts = to_utc(bar.ts)
        if self.pending and bar_ts >= self.pending["ts"]:
            self._fill_pending(bar)
            self.events.append(
                {"ts": bar_ts.isoformat(), "event": "entry_filled", "price": bar.open}
            )
        self._manage(bar)

    def _fill_pending(self, bar):
        decision = self.pending["decision"]
        size = self.pending["size"]
        direction = 1.0 if decision.action == "enter_long" else -1.0
        entry = self._slip(bar.open, direction)
        self.account.open_position(
            Position(
                symbol=bar.symbol,
                side="long" if direction > 0 else "short",
                qty=size,
                entry=entry,
                stop=decision.stop_loss,
                take_profit=decision.take_profit,
                opened_at=bar.ts,
                confidence=decision.confidence,
                rationale=decision.thesis,
                max_hold_min=decision.max_hold_minutes,
                regime=decision.regime,
            )
        )
        self.pending = None

    def _manage(self, bar):
        pos = self.account.position
        if pos is None or to_utc(bar.ts) <= to_utc(pos.opened_at):
            return
        direction = 1.0 if pos.side == "long" else -1.0
        pos.max_mfe = max(getattr(pos, "max_mfe", 0.0), (bar.high - pos.entry) * direction)
        pos.max_mae = min(getattr(pos, "max_mae", 0.0), (bar.low - pos.entry) * direction)
        stop_hit = bar.low <= pos.stop if pos.side == "long" else bar.high >= pos.stop
        tp_hit = (
            bar.high >= pos.take_profit if pos.side == "long" else bar.low <= pos.take_profit
        )
        held_min = (to_utc(bar.ts) - to_utc(pos.opened_at)).total_seconds() / 60.0
        if stop_hit:
            self._close(bar, pos.stop, "stop", direction, bar.ts)
        elif tp_hit:
            self._close(bar, pos.take_profit, "take_profit", direction, bar.ts)
        elif held_min >= pos.max_hold_min:
            self._close(bar, bar.close, "max_hold", direction, bar.ts)
        elif minutes_to_close(bar.ts) <= 1:
            self._close(bar, bar.close, "session_end", direction, bar.ts)

    def _close(self, bar, exit_price, reason, direction, ts):
        pos = self.account.position
        exit_fill = self._slip(exit_price, -direction)
        gross = (exit_fill - pos.entry) * direction * pos.qty
        commission = self.cfg.commission_per_share * pos.qty * 2
        pnl = gross - commission
        risk = pos.risk_per_unit() * pos.qty
        trade = Trade(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry=pos.entry,
            exit=exit_fill,
            stop=pos.stop,
            take_profit=pos.take_profit,
            opened_at=pos.opened_at,
            closed_at=to_utc(ts),
            pnl=pnl,
            r_multiple=pnl / risk if risk else 0.0,
            exit_reason=reason,
            confidence=pos.confidence,
            rationale=pos.rationale,
            max_favorable=getattr(pos, "max_mfe", 0.0),
            max_adverse=getattr(pos, "max_mae", 0.0),
            hold_min=int((to_utc(ts) - to_utc(pos.opened_at)).total_seconds() // 60),
            regime=pos.regime,
        )
        self.account.close_position(trade)
        update_halt(self.account, self.cfg)
        self.trades.append(trade)
        self.events.append(
            {
                "ts": to_utc(ts).isoformat(),
                "event": f"exit_{reason}",
                "pnl": round(pnl, 2),
                "r": round(trade.r_multiple, 2),
            }
        )

    def snapshot(self, price=None):
        if price is None and self.account.position is not None:
            price = self.account.position.entry
        return self.account.snapshot(price)

    def force_close(self, price, ts, reason="forced"):
        pos = self.account.position
        if pos is None:
            return None
        direction = 1.0 if pos.side == "long" else -1.0
        bar = type("B", (), {"close": price, "high": price, "low": price, "ts": ts})()
        self._close(bar, price, reason, direction, ts)
        self.pending = None
        return self.trades[-1]
