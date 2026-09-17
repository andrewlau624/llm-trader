"""Is this machine actually able to run for a week?"""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def main():
    problems, warnings = [], []
    head = ""
    if shutil.which("pmset"):
        power = sh(["pmset", "-g", "ps"]).splitlines()
        head = power[0] if power else ""
        if "AC Power" in head:
            print("  ok    on AC power")
        else:
            warnings.append(
                "on battery (" + (head[:60] if head else "unknown") + "). caffeinate -s holds "
                "its anti-sleep assertion only on AC. The loop will pause when the machine "
                "sleeps, and closing the lid sleeps it anyway. Plug in or use a server "
                "(docs/remote.md)."
            )
        if head:
            print(f"  info  power: {head[:70]}")
    else:
        print("  info  power: not a laptop, or no pmset (fine on Linux)")

    from llmtrader.config import alpaca_keys

    key, secret = alpaca_keys()
    if not (key and secret):
        problems.append("ALPACA keys missing in .env")
    else:
        try:
            from alpaca.trading.client import TradingClient

            acct = TradingClient(key, secret, paper=True).get_account()
            print(f"  ok    alpaca paper reachable, equity {float(acct.equity):,.2f}")
        except Exception as e:
            problems.append(f"alpaca unreachable: {str(e)[:70]}")

    from llmtrader.priors import DEFAULT_PATH, load_priors

    priors = load_priors()
    if not priors:
        problems.append(f"no priors at {DEFAULT_PATH}; run: make study")
    else:
        print(f"  ok    priors from {priors.get('sessions')} sessions, "
              f"{priors.get('windows'):,} windows, {priors.get('generated_at', '')[:10]}")

    from llmtrader.config import Config

    cfg = Config.load()
    print(f"  info  notional cap {cfg.max_notional_pct:.0f}%/trade, "
          f"risk target {cfg.risk_per_trade_pct}%/trade, basket {', '.join(cfg.basket)}")
    if cfg.max_notional_pct < 100:
        warnings.append(
            f"max_notional_pct is {cfg.max_notional_pct:.0f}. A 1-ATR stop wants about 147% of "
            f"equity, so this cap binds and trades run far below the intended risk. Pass "
            f"--notional-pct 100 (make week does)."
        )
    if not shutil.which("caffeinate"):
        print("  info  caffeinate absent (Linux). make week runs without it; "
              "make service is the better option here")

    print()
    for w in warnings:
        print(f"  WARN  {w}")
    for p in problems:
        print(f"  FAIL  {p}")
    if problems:
        print("\n  not ready. fix the failures above.\n")
        return 1
    print("\n  ready.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
