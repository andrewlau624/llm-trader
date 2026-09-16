from dataclasses import dataclass
from datetime import datetime


class BrokerError(RuntimeError):
    pass


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry: float
    stop: float
    take_profit: float
    opened_at: datetime
    confidence: int = 0
    rationale: str = ""
    max_hold_min: int = 240

    def unrealized(self, price):
        direction = 1.0 if self.side == "long" else -1.0
        return (price - self.entry) * direction * self.qty

    def risk_per_unit(self):
        return abs(self.entry - self.stop)

    def r_multiple(self, price):
        risk = self.risk_per_unit() * self.qty
        if risk == 0:
            return 0.0
        return self.unrealized(price) / risk


@dataclass
class Trade:
    symbol: str
    side: str
    qty: float
    entry: float
    exit: float
    stop: float
    take_profit: float
    opened_at: datetime
    closed_at: datetime
    pnl: float
    r_multiple: float
    exit_reason: str
    confidence: int = 0
    rationale: str = ""
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    hold_min: int = 0

    def to_dict(self):
        d = dict(self.__dict__)
        d["opened_at"] = self.opened_at.isoformat()
        d["closed_at"] = self.closed_at.isoformat()
        return d


@dataclass
class AccountState:
    equity: float
    cash: float
    realized_pnl: float = 0.0
    day_pnl: float = 0.0
    day_start_equity: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    wins: int = 0
    losses: int = 0
    position: Position = None
    halted: bool = False
    halt_reason: str = ""
    day: object = None

    def day_return_pct(self):
        if not self.day_start_equity:
            return 0.0
        return self.day_pnl / self.day_start_equity * 100.0
