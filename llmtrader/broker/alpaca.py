from datetime import datetime, timedelta, timezone

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

    def __init__(self, cfg, api_key=None, secret_key=None, account=None):
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
        self.open_entry = None
        self.trades = []
        self.events = []
        self._last_equity = None

    def _api_account(self):
        return self.client.get_account()

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

    def _open_position(self):
        try:
            pos = self.client.get_open_position(self.cfg.symbols[0])
        except Exception:
            return None
        qty = abs(float(pos.qty))
        entry = float(pos.avg_entry_price)
        side = "long" if float(pos.qty) > 0 else "short"
        stop = tp = None
        opened = to_utc(datetime.now(timezone.utc))
        if self.open_entry:
            stop = self.open_entry.get("stop")
            tp = self.open_entry.get("take_profit")
            opened = self.open_entry.get("opened_at", opened)
        return Position(
            symbol=pos.symbol,
            side=side,
            qty=qty,
            entry=entry,
            stop=stop,
            take_profit=tp,
            opened_at=opened,
            confidence=(self.open_entry or {}).get("confidence", 0),
            rationale=(self.open_entry or {}).get("rationale", ""),
            regime=(self.open_entry or {}).get("regime", ""),
            max_hold_min=(self.open_entry or {}).get("max_hold_min", self.cfg.max_hold_min),
        )

    def sync(self, now=None):
        now = to_utc(now or datetime.now(timezone.utc))
        self.start_day_if_needed(now)
        pos = self._open_position()
        if pos is not None:
            self.book.position = pos
            return []
        if self.book.position is None and not self.open_entry:
            return []
        closed = self._resolve_closed_trade(now)
        self.book.position = None
        self.open_entry = None
        if closed:
            self.trades.append(closed)
            self.book.close_position(closed)
            update_halt(self.book, self.cfg)
            self.events.append(
                {
                    "ts": now.isoformat(),
                    "event": f"exit_{closed.exit_reason}",
                    "pnl": round(closed.pnl, 2),
                    "r": round(closed.r_multiple, 2),
                }
            )
            return [closed]
        return []

    def _resolve_closed_trade(self, now):
        entry_info = self.open_entry or {}
        entry_price = entry_info.get("entry")
        exit_price = None
        reason = "unknown"
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED,
                symbols=[self.cfg.symbols[0]],
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

        symbol = self.cfg.symbols[0]
        now = to_utc(now or datetime.now(timezone.utc))
        if not in_rth(now) and not self.cfg.allow_after_hours:
            raise BrokerError(
                "refusing to submit outside regular trading hours: a DAY order placed now "
                "could fill at the next open at a price unrelated to this decision"
            )
        qty = max(1, int(size))
        try:
            base_price = self.live_price(symbol)
        except Exception as e:
            base_price = None
            self.events.append(
                {"event": "live_price_unavailable", "error": str(e)[:200],
                 "ts": (now or datetime.now(timezone.utc)).isoformat()}
            )
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
        self.open_entry = {
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

    def cancel_open_orders(self):
        try:
            self.client.cancel_orders()
            self.events.append({"event": "cancelled_open_orders"})
        except Exception as e:
            self.events.append({"event": "cancel_orders_failed", "error": str(e)[:200]})
        self.open_entry = None

    def close_all(self):
        try:
            self.client.close_all_positions(cancel_orders=True)
        except Exception as e:
            self.events.append({"event": "close_all_failed", "error": str(e)})

    def account_state(self, price=None):
        try:
            acct = self._api_account()
            self.book.set_balance(float(acct.equity))
        except Exception:
            self.book.refresh_day_pnl()
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
