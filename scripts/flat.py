"""Close everything, right now, and verify it.

Use when you need the account flat: before a fresh run, after an incident, or when a position has
lost its stop. Cancels each symbol's orders, closes the position, and checks the result.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.broker.alpaca import AlpacaBroker
from llmtrader.config import Config


def main():
    cfg = Config.load()
    broker = AlpacaBroker(cfg)
    positions = broker.client.get_all_positions()
    print()
    if not positions:
        print("  no open positions")
    else:
        for p in positions:
            print(f"  position {p.symbol} qty {p.qty} avg {p.avg_entry_price} "
                  f"unrealized {p.unrealized_pl}")
    orders = broker.open_order_summary()
    for o in orders:
        print(f"  open order {o['symbol']} {o['type']} {o['side']} qty {o['qty']} {o['status']}")
    if not positions and not orders:
        print("  already flat")
        print()
        return 0

    print("\n  flattening...")
    for outcome in broker.flatten(reason="manual_flat"):
        ok = outcome["event"] == "flattened"
        print(f"    {outcome['event']} {outcome.get('symbol')}"
              + ("" if ok else "   <-- NEEDS ATTENTION"))

    # Deliberately no blanket cancel here. When the market is shut, the close submitted above is
    # queued and shows as an open order; cancelling every open order would destroy the very close
    # we just asked for and leave the position naked. That mistake was already made once.

    left = broker.client.get_all_positions()
    print()
    if left:
        for p in left:
            print(f"  STILL OPEN: {p.symbol} qty {p.qty}")
        print("  A queued order may still be pending; check after the next open.")
    else:
        print("  flat, no positions")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
