import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.config import Config, alpaca_keys

OK = "  ok   "
WARN = " warn  "
FAIL = " fail  "


def line(status, label, detail=""):
    print(f"{status}{label:<34}{detail}")


def check_python():
    v = sys.version_info
    line(OK if v >= (3, 10) else FAIL, "python", f"{v.major}.{v.minor}.{v.micro}")


def check_ollama(cfg):
    try:
        with urllib.request.urlopen(f"{cfg.ollama_host}/api/tags", timeout=5) as r:
            data = json.load(r)
    except Exception as e:
        line(FAIL, "ollama reachable", f"{cfg.ollama_host} -> {e}")
        return
    names = [m.get("name") for m in data.get("models", [])]
    line(OK, "ollama reachable", f"{len(names)} models")
    if cfg.ollama_model in names:
        line(OK, f"model {cfg.ollama_model}", "installed")
    else:
        line(FAIL, f"model {cfg.ollama_model}", "run: ollama pull " + cfg.ollama_model)


def check_alpaca(cfg):
    key, secret = alpaca_keys()
    if not (key and secret):
        line(WARN, "alpaca keys",
             "not set (fine for replay/sim; needed for make live)")
        return
    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(key, secret, paper=True)
        acct = client.get_account()
        line(OK, "alpaca paper account", f"equity {float(acct.equity):,.2f} status {acct.status}")
    except Exception as e:
        line(FAIL, "alpaca paper account", str(e)[:80])


def check_data(cfg):
    try:
        from llmtrader.data.feeds import get_feed

        feed = get_feed(cfg.data_source, frozen=True)
        bars = feed.bars_1m(cfg.symbols[0], days=3, cache_age_s=0)
    except Exception as e:
        line(FAIL, f"{cfg.data_source} 1m data", str(e)[:80])
        return
    if not bars:
        line(FAIL, f"{cfg.data_source} 1m data", "no bars returned")
        return
    days = sorted({b.et.date() for b in bars})
    span = (bars[-1].ts - bars[0].ts).total_seconds() / 86400
    line(OK, f"{cfg.symbols[0]} 1m data",
         f"{len(bars)} bars, {len(days)} sessions, {span:.1f} days")
    line(OK, "latest bar",
         f"{bars[-1].et:%Y-%m-%d %H:%M} ET "
         f"({(datetime.now(timezone.utc) - bars[-1].ts).total_seconds() / 60:.0f} min ago)")


def check_backend(cfg):
    if cfg.llm_backend == "ollama":
        return
    from llmtrader.config import get_env

    needed = {"deepseek": "DEEPSEEK_API_KEY", "opencode-go": "OPENCODE_API_KEY"}
    var = needed.get(cfg.llm_backend)
    have = get_env(var) if var else None
    line(OK if have else FAIL, f"{cfg.llm_backend} key", var if have else f"{var} missing in .env")


def main():
    ap = argparse.ArgumentParser(description="Verify the environment is ready")
    ap.parse_args()
    cfg = Config.load()
    print()
    print("llm-trader environment check")
    print("-" * 62)
    check_python()
    check_ollama(cfg)
    check_backend(cfg)
    check_data(cfg)
    check_alpaca(cfg)
    print("-" * 62)
    print(f"  backend {cfg.llm_backend} / {cfg.ollama_model}")
    print(f"  symbol {cfg.symbols[0]}  interval {cfg.decision_interval_min}m  "
          f"gate {'on' if cfg.llm_gate else 'off'}")
    print(f"  risk {cfg.risk_per_trade_pct}%/trade  max {cfg.max_trades_per_day}/day  "
          f"halt at -{cfg.max_daily_loss_pct}%/day")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
