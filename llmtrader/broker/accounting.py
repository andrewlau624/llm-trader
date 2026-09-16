from .base import AccountState


class LocalAccount:
    def __init__(self, equity=100000.0):
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

    def start_day(self, day, equity=None):
        if self.day == day:
            return
        self.day = day
        self.day_start_equity = equity if equity is not None else self.equity
        self.day_pnl = 0.0
        self.trades_today = 0
        self.halted = False
        self.halt_reason = ""
        self.consecutive_losses = self.consecutive_losses

    def open_position(self, position):
        self.position = position
        self.trades_today += 1

    def close_position(self, trade):
        self.position = None
        self.realized_pnl += trade.pnl
        self.day_pnl += trade.pnl
        self.equity = self.start_equity + self.realized_pnl
        if trade.pnl >= 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
        self.equity_curve.append((trade.closed_at.isoformat(), round(self.equity, 2)))
        return trade

    def mark(self, price):
        unrealized = self.position.unrealized(price) if self.position is not None else 0.0
        self.equity = self.start_equity + self.realized_pnl + unrealized
        return self.equity

    def snapshot(self, price=None):
        if price is not None:
            self.mark(price)
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
