import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..data.base import in_rth, to_utc
from ..risk import update_halt
from .accounting import LocalAccount
from .base import BrokerError, Position, Trade


def bracket_problems(action, stop, take_profit, base_price, tick=0.01):
    problems = []
    if base_price is None:
        return problems
    if action == "enter_long":
        if stop is not None and stop > base_price - tick:
            problems.append(
                f"long stop {stop} must be <= live price {base_price:.2f} - {tick}"
            )
        if take_profit is not None and take_profit < base_price + tick:
            problems.append(
                f"long target {take_profit} must be >= live price {base_price:.2f} + {tick}"
            )
    else:
        if stop is not None and stop < base_price + tick:
            problems.append(
                f"short stop {stop} must be >= live price {base_price:.2f} + {tick}"
            )
        if take_profit is not None and take_profit > base_price - tick:
            problems.append(
                f"short target {take_profit} must be <= live price {base_price:.2f} - {tick}"
            )
    return problems


class AlpacaBroker:
    name = "alpaca"

    def __init__(self, cfg, api_key=None, secret_key=None, account=None, state_dir=None):
        from alpaca.trading.client import TradingClient

        from ..config import alpaca_keys

        key, secret = alpaca_keys()
        self.api_key = api_key or key
        self.secret_key = secret_key or secret
        if not (self.api_key and self.secret_key):
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY missing - add paper keys to .env"
            )
        self.client = TradingClient(self.api_key, self.secret_key, paper=True)
        self.data_client = None
        self.cfg = cfg
        self.book = account or LocalAccount(cfg.equity, authoritative_equity=True)
        from ..config import ROOT
        from ..lock import account_fingerprint

        state_root = Path(state_dir) if state_dir else Path(ROOT) / "state"
        self.account_fingerprint = account_fingerprint(self.api_key, self.secret_key)
        self.state_path = state_root / f"book-{self.account_fingerprint}.json"
        # Every attribute _restore_state() touches must exist before it is called. It previously
        # ran before self.events existed, which made the constructor raise, so nothing could start.
        self.open_entry = None
        self.open_entries = {}
        self.trades = []
        self.events = []
        self._measured = set()
        self._last_equity = None
        self._restore_state()

    def _api_account(self):
        return self.client.get_account()

    def _restore_state(self):
        if not self.state_path.exists():
            return
        if not hasattr(self, "events"):
            self.events = []
        try:
            self.book.restore(json.loads(self.state_path.read_text()))
            self.events.append({"event": "state_restored", "day": str(self.book.day),
                                "trades_today": self.book.trades_today,
                                "halted": self.book.halted})
        except Exception as e:
            self.events.append({"event": "state_restore_failed", "error": str(e)[:200]})

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.book.state(), indent=2))
        except Exception as e:
            self.events.append({"event": "state_save_failed", "error": str(e)[:200]})

    def live_price(self, symbol=None):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        symbol = symbol or self.cfg.symbols[0]
        if self.data_client is None:
            self.data_client = StockHistoricalDataClient(self.api_key, self.secret_key)
        req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self.cfg.bar_feed)
        data = self.data_client.get_stock_latest_trade(req)
        trade = data.get(symbol) if hasattr(data, "get") else data[symbol]
        return float(trade.price)

    def start_day_if_needed(self, now):
        et = to_utc(now).astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
        if self.book.day != et.date():
            acct = self._api_account()
            equity = float(acct.equity)
            self.book.set_balance(equity)
            self.book.start_day(et.date(), equity=equity)

    def symbols(self):
        return list(dict.fromkeys(self.cfg.symbols))

    def _open_position(self, symbol=None):
        symbol = symbol or self.cfg.symbols[0]
        try:
            pos = self.client.get_open_position(symbol)
        except Exception:
            return None
        qty = abs(float(pos.qty))
        entry = float(pos.avg_entry_price)
        side = "long" if float(pos.qty) > 0 else "short"
        entry_info = self.open_entries.get(symbol) or (
            self.open_entry if self.open_entry and self.open_entry.get("symbol") == symbol else {}
        )
        stop = tp = None
        opened = to_utc(datetime.now(timezone.utc))
        if entry_info:
            stop = entry_info.get("stop")
            tp = entry_info.get("take_profit")
            opened = entry_info.get("opened_at", opened)
        return Position(
            symbol=pos.symbol,
            side=side,
            qty=qty,
            entry=entry,
            stop=stop,
            take_profit=tp,
            opened_at=opened,
            confidence=entry_info.get("confidence", 0),
            rationale=entry_info.get("rationale", ""),
            regime=entry_info.get("regime", ""),
            max_hold_min=entry_info.get("max_hold_min", self.cfg.max_hold_min),
        )

    def measure_slippage(self):
        """Returns fill-vs-signal records for any newly filled entry, once each."""
        out = []
        for symbol in list(self.open_entries):
            rec = self._measure_one(symbol)
            if rec:
                out.append(rec)
        return out

    def _measure_one(self, symbol=None):
        """What the fill actually cost versus the price the signal was written against.

        This is the single number that decides whether the backtest means anything: the strategy's
        entire edge is ~14 bps per trade and break-even sits near 16 bps round trip, so real
        execution versus the modelled 3 bps is the difference between profit and loss.
        """
        symbol = symbol or self.cfg.symbols[0]
        info = self.open_entries.get(symbol)
        if not info or not info.get("signal_price"):
            return None
        measured = getattr(self, "_measured", None)
        if measured is None:
            measured = self._measured = set()
        if str(info.get("entry_order_id")) in measured:
            return None
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
        except Exception:
            return None
        try:
            orders = self.client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.ALL, symbols=[symbol], limit=20
            ))
        except Exception:
            return None
        for o in orders:
            if str(o.id) != str(info.get("entry_order_id")) or not o.filled_avg_price:
                continue
            fill = float(o.filled_avg_price)
            signal = float(info["signal_price"])
            direction = 1.0 if info.get("side") == "long" else -1.0
            cost_bps = (fill - signal) * direction / signal * 10000.0
            record = {
                "ts": to_utc(o.filled_at).isoformat() if o.filled_at else None,
                "symbol": symbol,
                "event": "fill_vs_signal",
                "signal_price": round(signal, 4),
                "fill_price": round(fill, 4),
                "cost_bps": round(cost_bps, 2),
                "expected_bps": info.get("expected_bps"),
                "modelled_bps": self.cfg.slippage_bps,
            }
            measured.add(str(info.get("entry_order_id")))
            self.events.append(record)
            return record
        return None

    def sync(self, now=None):
        """Reconcile every symbol in the basket. Returns closed trades."""
        now = to_utc(now or datetime.now(timezone.utc))
        self.start_day_if_needed(now)
        closed_out = []
        for symbol in self.symbols():
            pos = self._open_position(symbol)
            if pos is not None:
                self.book.position = pos
                self._open_symbol = symbol
                continue
            pending = self.open_entries.get(symbol)
            if pending is None and not (
                self.open_entry and self.open_entry.get("symbol") == symbol
            ):
                continue
            closed = self._resolve_closed_trade(now, symbol)
            self.open_entries.pop(symbol, None)
            if self.open_entry and self.open_entry.get("symbol") == symbol:
                self.open_entry = None
            if self.book.position is not None and self.book.position.symbol == symbol:
                self.book.position = None
            if closed:
                self.trades.append(closed)
                self.book.close_position(closed)
                update_halt(self.book, self.cfg)
                self._save_state()
                self.events.append({
                    "ts": now.isoformat(),
                    "event": f"exit_{closed.exit_reason}",
                    "pnl": round(closed.pnl, 2),
                    "r": round(closed.r_multiple, 2),
                })
                closed_out.append(closed)
        return closed_out

    def _resolve_closed_trade(self, now, symbol=None):
        symbol = symbol or self.cfg.symbols[0]
        entry_info = self.open_entries.get(symbol) or (
            self.open_entry or {}
        )
        entry_price = entry_info.get("entry")
        exit_price = None
        reason = "unknown"
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                symbols=[symbol],
                after=now - timedelta(days=2),
                limit=50,
            )
            orders = self.client.get_orders(req)
        except Exception:
            orders = []
        for o in orders:
            if entry_info.get("entry_order_id") and str(o.id) == str(entry_info["entry_order_id"]):
                continue
            if o.filled_avg_price is None:
                continue
            exit_price = float(o.filled_avg_price)
            otype = str(getattr(o, "type", "")).lower()
            if "stop" in otype:
                reason = "stop"
            elif "limit" in otype:
                reason = "take_profit"
            elif "market" in otype:
                reason = "market"
            if o.filled_at:
                now = max(now, to_utc(o.filled_at))
            break
        if entry_price is None or exit_price is None:
            return None
        qty = entry_info.get("qty", 0.0)
        direction = 1.0 if entry_info.get("side") == "long" else -1.0
        gross = (exit_price - entry_price) * direction * qty
        commission = self.cfg.commission_per_share * qty * 2
        stop = entry_info.get("stop")
        risk = abs(entry_price - stop) * qty if stop else 0.0
        opened_at = entry_info.get("opened_at", now)
        return Trade(
            symbol=self.cfg.symbols[0],
            side=entry_info.get("side", "long"),
            qty=qty,
            entry=entry_price,
            exit=exit_price,
            stop=stop or 0.0,
            take_profit=entry_info.get("take_profit") or 0.0,
            opened_at=opened_at,
            closed_at=now,
            pnl=gross - commission,
            r_multiple=(gross - commission) / risk if risk else 0.0,
            exit_reason=reason,
            confidence=entry_info.get("confidence", 0),
            rationale=entry_info.get("rationale", ""),
            hold_min=int((now - opened_at).total_seconds() // 60),
            regime=entry_info.get("regime", ""),
        )

    def submit(self, decision, size, now=None):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        symbol = getattr(decision, "symbol", "") or self.cfg.symbols[0]
        now = to_utc(now or datetime.now(timezone.utc))
        if not in_rth(now) and not self.cfg.allow_after_hours:
            raise BrokerError(
                "refusing to submit outside regular trading hours: a DAY order placed now "
                "could fill at the next open at a price unrelated to this decision"
            )
        base_price = None
        try:
            base_price = self.live_price(symbol)
        except Exception as e:
            self.events.append(
                {"event": "live_price_unavailable", "error": str(e)[:200],
                 "ts": now.isoformat()}
            )
        signal_price = decision.entry
        if base_price and signal_price:
            # Re-anchor the bracket to the live price, preserving the intended distances. The
            # decision was written against the last completed bar's close; the market order fills
            # seconds later at the live price. Leaving the legs on the signal price would refuse
            # precisely those entries where price ran, which are the ones most likely to have
            # stopped out, and would quietly flatter the live sample.
            stop_offset = decision.stop_loss - signal_price
            target_offset = decision.take_profit - signal_price
            decision.stop_loss = round(base_price + stop_offset, 2)
            decision.take_profit = round(base_price + target_offset, 2)
            decision.entry = round(base_price, 4)
            self.events.append({
                "ts": now.isoformat(), "event": "bracket_reanchored",
                "signal_price": round(signal_price, 4),
                "live_price": round(base_price, 4),
                "drift_bps": round((base_price - signal_price) / signal_price * 10000.0, 2),
                "stop": decision.stop_loss, "take_profit": decision.take_profit,
            })
        if base_price and size < 1.0:
            raise BrokerError(
                f"position size is {size:.3f} shares but Alpaca bracket orders only accept whole "
                f"shares, and rounding up to 1 share would be ${base_price:,.0f} of notional "
                f"({base_price / self.book.equity * 100:.0f}% of equity). Raise max_notional_pct "
                f"deliberately if that is what you intend, or fund the account further."
            )
        qty = max(1, int(size))
        problems = bracket_problems(
            decision.action, decision.stop_loss, decision.take_profit, base_price
        )
        if problems:
            msg = "; ".join(problems)
            self.events.append(
                {"event": "bracket_rejected", "reason": msg,
                 "live_price": base_price, "ts": (now or datetime.now(timezone.utc)).isoformat()}
            )
            raise BrokerError(
                f"bracket legs are invalid at the live price ({base_price}): {msg}"
            )
        side = OrderSide.BUY if decision.action == "enter_long" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=round(decision.stop_loss, 2)),
            take_profit=TakeProfitRequest(limit_price=round(decision.take_profit, 2)),
        )
        try:
            order = self.client.submit_order(req)
        except Exception as e:
            self.events.append(
                {"event": "order_rejected", "error": str(e)[:300],
                 "ts": (now or datetime.now(timezone.utc)).isoformat()}
            )
            raise BrokerError(f"alpaca rejected the order: {str(e)[:300]}") from e
        self.open_entry = self.open_entries[symbol] = {
            "symbol": symbol,
            "signal_price": getattr(decision, "entry", None),
            "expected_bps": getattr(decision, "expected_bps", None),
            "entry_order_id": str(order.id),
            "side": decision.side,
            "qty": float(qty),
            "entry": round(decision.entry, 4),
            "stop": decision.stop_loss,
            "take_profit": decision.take_profit,
            "confidence": decision.confidence,
            "rationale": decision.thesis,
            "regime": decision.regime,
            "max_hold_min": decision.max_hold_minutes,
            "opened_at": to_utc(now or datetime.now(timezone.utc)),
        }
        self.book.trades_today += 1
        self.events.append(
            {"ts": (now or datetime.now(timezone.utc)).isoformat(), "event": "order_submitted",
             "qty": qty, "side": decision.side, "order_id": str(order.id)}
        )
        return order

    enter = submit

    def orders_for(self, symbol):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            return self.client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol]
            ))
        except Exception:
            return []

    def unprotected_positions(self):
        """Positions with neither a working stop nor a pending close.

        The one state that must never persist overnight, and the one thing that cannot be fixed
        while the market is shut - so it has to be detected and re-armed as soon as a process is
        alive, not only during the session.
        """
        out = []
        for symbol in self.symbols():
            if self._open_position(symbol) is None:
                continue
            types = [str(o.type).lower() for o in self.orders_for(symbol)]
            if not any("stop" in t for t in types) and not any("market" in t for t in types):
                out.append(symbol)
        return out

    def position_report(self):
        rows = []
        for p in self.client.get_all_positions():
            orders = [str(o.type) for o in self.orders_for(p.symbol)]
            # Alpaca reports these as "OrderType.STOP" / "OrderType.MARKET" - uppercase. Matching
            # on "Market" made this flag permanently False, so every startup would have warned
            # about an unprotected position that was in fact protected.
            lowered = [o.lower() for o in orders]
            rows.append({
                "symbol": p.symbol, "qty": p.qty, "entry": p.avg_entry_price,
                "unrealized": p.unrealized_pl, "orders": orders,
                "protected": any("stop" in o for o in lowered)
                or any("market" in o for o in lowered),
            })
        return rows

    def open_order_summary(self):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        except Exception:
            return []
        return [{"symbol": str(o.symbol), "type": str(o.type), "side": str(o.side),
                 "qty": str(o.qty), "status": str(o.status)} for o in orders]

    def cancel_open_orders(self):
        try:
            self.client.cancel_orders()
            self.events.append({"event": "cancelled_open_orders"})
        except Exception as e:
            self.events.append({"event": "cancel_orders_failed", "error": str(e)[:200]})
        self.open_entry = None

    def flatten(self, symbols=None, reason="session_end", attempts=4):
        """Close positions one symbol at a time, cancelling only that symbol's orders, then verify.

        Deliberately NOT close_all_positions(cancel_orders=True): that cancels every order on the
        account, so a second process running against the same account kills this one's closing
        order. It did exactly that, and left a short open and unprotected overnight.
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        outcomes = []
        for symbol in (symbols or self.symbols()):
            if self._open_position(symbol) is None:
                continue
            for order in self.client.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol]
            )):
                try:
                    self.client.cancel_order_by_id(order.id)
                except Exception as e:
                    self.events.append({"event": "cancel_failed", "symbol": symbol,
                                        "error": str(e)[:150]})
            try:
                self.client.close_position(symbol)
            except Exception as e:
                self.events.append({"event": "close_failed", "symbol": symbol, "reason": reason,
                                    "error": str(e)[:200]})
            flat = False
            for _ in range(attempts):
                time.sleep(1.5)
                if self._open_position(symbol) is None:
                    flat = True
                    break
            record = {"event": "flattened" if flat else "FLATTEN_FAILED", "symbol": symbol,
                      "reason": reason, "ts": datetime.now(timezone.utc).isoformat()}
            self.events.append(record)
            outcomes.append(record)
        return outcomes

    def close_all(self, reason="session_end"):
        return self.flatten(reason=reason)

    def ensure_protection(self, symbol, atr, side=None):
        """A position with no working stop is the one state that must never persist.

        Happens after a restart, or after a cancelled stop as above. Re-attach a stop one ATR away
        rather than trusting that someone will notice.
        """
        from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
        from alpaca.trading.requests import GetOrdersRequest, StopOrderRequest

        pos = self._open_position(symbol)
        if pos is None or not atr:
            return None
        for order in self.client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN, symbols=[symbol]
        )):
            otype = str(order.type).lower()
            if "stop" in otype:
                return None
            if "market" in otype:
                # A queued closing order is already on its way. Attaching a stop now would sit on
                # a position that is about to disappear, and could over-close into a long.
                self.events.append({"event": "protection_skipped_pending_close",
                                    "symbol": symbol, "order_id": str(order.id)})
                return None
        long_side = pos.side == "long"
        stop = round(pos.entry - atr, 2) if long_side else round(pos.entry + atr, 2)
        try:
            order = self.client.submit_order(StopOrderRequest(
                symbol=symbol, qty=pos.qty,
                side=OrderSide.SELL if long_side else OrderSide.BUY,
                time_in_force=TimeInForce.DAY, stop_price=stop,
            ))
            self.events.append({"event": "protection_reattached", "symbol": symbol,
                                "stop": stop, "qty": pos.qty})
            return order
        except Exception as e:
            self.events.append({"event": "PROTECTION_FAILED", "symbol": symbol,
                                "error": str(e)[:200]})
            return None

    def account_state(self, price=None):
        try:
            acct = self._api_account()
            self.book.set_balance(float(acct.equity))
        except Exception:
            self.book.refresh_day_pnl()
        self._save_state()
        return self.book.snapshot()

    def forced_exit_due(self, now, price):
        pos = self.book.position
        if pos is None:
            return None
        held = (to_utc(now) - to_utc(pos.opened_at)).total_seconds() / 60.0
        if held >= pos.max_hold_min:
            return "max_hold"
        from ..data.base import minutes_to_close

        if minutes_to_close(now) <= self.cfg.no_entry_last_min:
            return "session_end"
        return None
