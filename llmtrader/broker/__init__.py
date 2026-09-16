from .accounting import LocalAccount
from .base import AccountState, Position, Trade

__all__ = ["AccountState", "LocalAccount", "Position", "Trade", "get_broker"]


def get_broker(name, cfg, account=None):
    name = (name or "sim").lower()
    if name == "alpaca":
        from .alpaca import AlpacaBroker

        return AlpacaBroker(cfg, account=account)
    if name == "sim":
        from .sim import SimBroker

        return SimBroker(cfg, account=account)
    raise ValueError(f"unknown broker: {name}")
