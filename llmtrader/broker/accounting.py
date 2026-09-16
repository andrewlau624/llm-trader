from .base import AccountState


class LocalAccount:
    """Account state for the sim broker and the local risk book for the Alpaca broker.

    day_pnl is always the balance delta (equity - day_start_equity), never the sum of trade
    results. Trade sums miss commissions, slippage, partial fills and anything that happened
    outside this process, so a daily loss limit computed from them can be wrong by a large
    margin. realized_pnl is still tracked, but only for reporting.
    """

    def __init__(self, equity=100000.0, authoritative_equity=False):
        self.start_equity = equity
        self.equity = equity
        self.realized_pnl = 0.0
        self.day_start_equity = equity
        self.day_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.wins = 0
        self.losses = 0
        self.position = None
        self.halted = False
        self.halt_reason = ""
        self.day = None
        self.equity_curve = []
        self.authoritative_equity = authoritative_equity

    def start_day(self, day, equity=None):
        if self.day == day:
            return
        self.day = day
        self.day_start_equity = float(equity) if equity is not None else self.equity
        self.trades_today = 0
        self.halted = False
        self.halt_reason = ""
        self.refresh_day_pnl()

    def set_balance(self, equity):
        self.equity = float(equity)
        self.refresh_day_pnl()
        return self.equity

    def refresh_day_pnl(self):
        self.day_pnl = self.equity - self.day_start_equity
        return self.day_pnl

    def open_position(self, position):
        self.position = position
        self.trades_today += 1

    def close_position(self, trade):
        self.position = None
        self.realized_pnl += trade.pnl
        if not self.authoritative_equity:
            self.equity = self.start_equity + self.realized_pnl
        if trade.pnl >= 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
        self.equity_curve.append((trade.closed_at.isoformat(), round(self.equity, 2)))
        self.refresh_day_pnl()
        return trade

    def mark(self, price=None):
        if not self.authoritative_equity:
            if self.position is not None and price is not None:
                unrealized = self.position.unrealized(price)
            else:
                unrealized = 0.0
            self.equity = self.start_equity + self.realized_pnl + unrealized
        self.refresh_day_pnl()
        return self.equity

    def snapshot(self, price=None):
        if price is not None:
            self.mark(price)
        else:
            self.refresh_day_pnl()
        return AccountState(
            equity=self.equity,
            cash=self.start_equity + self.realized_pnl,
            realized_pnl=self.realized_pnl,
            day_pnl=self.day_pnl,
            day_start_equity=self.day_start_equity,
            trades_today=self.trades_today,
            consecutive_losses=self.consecutive_losses,
            wins=self.wins,
            losses=self.losses,
            position=self.position,
            halted=self.halted,
            halt_reason=self.halt_reason,
            day=self.day,
        )
