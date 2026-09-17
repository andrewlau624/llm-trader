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
INFO = " info  "


def line(status, label, detail=""):
    print(f"{status}{label:<34}{detail}")


def check_python():
    v = sys.version_info
    line(OK if v >= (3, 10) else FAIL, "python", f"{v.major}.{v.minor}.{v.micro}")


def check_strategy(cfg):
    det = (cfg.strategy or "deterministic") == "deterministic"
    line(OK, "strategy", "deterministic reversal rule" if det else "llm")
    line(INFO, "basket", ", ".join(cfg.basket))
    cap = cfg.max_notional_pct
    if det and cap < 100:
        line(WARN, "notional cap",
             f"{cap:.0f}% - a 1-ATR stop wants ~147%, so this binds and trades run"
             f" far below the {cfg.risk_per_trade_pct}% risk target")
    else:
        line(OK, "risk budget", f"{cap:.0f}% notional, {cfg.risk_per_trade_pct}%/trade target")
    return det


def check_llm(cfg, deterministic):
    if deterministic:
        return
    backend = (cfg.llm_backend or "ollama").lower()
    if backend != "ollama":
        from llmtrader.config import get_env

        var = {"deepseek": "DEEPSEEK_API_KEY", "opencode-go": "OPENCODE_API_KEY"}.get(backend)
        have = get_env(var) if var else None
        line(OK if have else FAIL, f"{backend} key", var if have else f"{var} missing in .env")
        return
    try:
        with urllib.request.urlopen(f"{cfg.ollama_host}/api/tags", timeout=5) as r:
            data = json.load(r)
    except Exception as e:
        line(FAIL, "ollama reachable", f"{cfg.ollama_host} -> {str(e)[:50]}")
        return
    names = [m.get("name") for m in data.get("models", [])]
    line(OK, "ollama reachable", f"{len(names)} models")
    if cfg.ollama_model in names:
        line(OK, f"model {cfg.ollama_model}", "installed")
    else:
        line(FAIL, f"model {cfg.ollama_model}", "run: ollama pull " + cfg.ollama_model)


def check_priors(cfg, deterministic):
    if not deterministic:
        return
    from llmtrader.priors import load_priors

    priors = load_priors()
    if not priors:
        line(FAIL, "priors file", "missing - run: make priors")
        return
    gen = str(priors.get("generated_at", ""))[:10]
    line(OK, "priors", f"{priors.get('sessions')} sessions, {priors.get('windows'):,} windows, "
                       f"{priors.get('symbol')} {priors.get('granularity')}, generated {gen}")
    if priors.get("symbol") not in cfg.basket:
        line(WARN, "priors symbol", f"measured on {priors.get('symbol')}, basket is "
                                    f"{', '.join(cfg.basket)}")


def check_alpaca(cfg):
    key, secret = alpaca_keys()
    if not (key and secret):
        line(WARN, "alpaca keys", "not set - fine for scanning, needed to place paper orders")
        return None
    try:
        from alpaca.trading.client import TradingClient

        acct = TradingClient(key, secret, paper=True).get_account()
        line(OK, "alpaca paper account", f"equity {float(acct.equity):,.2f} "
                                         f"status {acct.status}")
        return float(acct.equity)
    except Exception as e:
        line(FAIL, "alpaca paper account", str(e)[:70])
        return None


def check_data(cfg):
    try:
        from llmtrader.data.feeds import get_feed

        feed = get_feed(cfg.data_source, frozen=True)
        bars = feed.bars_1m(cfg.basket[0], days=3, cache_age_s=0)
    except Exception as e:
        line(FAIL, f"{cfg.data_source} data", str(e)[:70])
        return
    if not bars:
        line(FAIL, f"{cfg.data_source} data", "no bars returned")
        return
    age_min = (datetime.now(timezone.utc) - bars[-1].ts).total_seconds() / 60
    line(OK, f"{cfg.basket[0]} 1m data", f"{len(bars)} bars, {len({b.et.date() for b in bars})} "
                                         f"sessions")
    status = OK if age_min < 60 else WARN
    line(status, "latest bar", f"{bars[-1].et:%Y-%m-%d %H:%M} ET ({age_min:.0f} min ago)")
    try:
        missing = [s for s in cfg.basket
                   if not feed.bars_1m(s, days=1, cache_age_s=0)]
        if missing:
            line(FAIL, "basket coverage", f"no data for {', '.join(missing)}")
        else:
            line(OK, "basket coverage", f"all {len(cfg.basket)} symbols have data")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Verify the environment is ready")
    ap.parse_args()
    cfg = Config.load()
    print()
    print("llm-trader environment check")
    print("-" * 66)
    check_python()
    deterministic = check_strategy(cfg)
    check_priors(cfg, deterministic)
    check_alpaca(cfg)
    check_data(cfg)
    check_llm(cfg, deterministic)
    print("-" * 66)
    print(f"  equity {cfg.equity:,.0f} | interval {cfg.decision_interval_min}m | "
          f"gate {'on' if cfg.llm_gate else 'off'}")
    print("  next:  make priors   (if missing)   then   make week")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
