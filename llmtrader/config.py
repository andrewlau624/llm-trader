import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path=None):
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    symbols: list = field(default_factory=lambda: ["SPY"])
    decision_interval_min: int = 5
    timeframes: list = field(default_factory=lambda: ["1m", "5m", "1h"])
    bars_per_timeframe: int = 15
    warmup_bars: int = 220
    extra_context_symbols: list = field(default_factory=lambda: ["QQQ", "IWM", "VIXY"])
    data_source: str = "yfinance"
    bar_feed: str = "iex"

    llm_backend: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    llm_temperature: float = 0.2
    llm_timeout_s: int = 180
    llm_retries: int = 2
    shadow_models: list = field(default_factory=list)

    min_confidence: int = 6
    min_reward_risk: float = 1.5
    require_regime_alignment: bool = True
    min_confidence_counter_trend: int = 9
    max_entry_slip_pct: float = 0.15
    require_stop: bool = True

    equity: float = 100000.0
    risk_per_trade_pct: float = 0.25
    max_notional_pct: float = 10.0
    max_trades_per_day: int = 6
    max_daily_loss_pct: float = 1.0
    max_consecutive_losses: int = 3
    min_confidence_after_losses: int = 8
    no_entry_first_min: int = 5
    no_entry_last_min: int = 10
    allow_after_hours: bool = False
    max_hold_min: int = 240
    allow_shorts: bool = True

    commission_per_share: float = 0.0
    slippage_bps: float = 1.5
    fill_at: str = "next_bar_open"

    journal_dir: str = "runs"
    log_raw_responses: bool = True
    call_llm_when_halted: bool = False
    call_llm_in_position: bool = False

    news_urls: list = field(default_factory=list)
    news_lookback_min: int = 180
    news_max_items: int = 6

    llm_gate: bool = False
    gate_min_adx: float = 25.0
    gate_min_relvol: float = 1.5
    gate_min_bbwidth: float = 0.25
    gate_min_abs_zscore: float = 1.5
    gate_min_vwap_dist: float = 0.25
    gate_min_session_rvol: float = 1.4

    def __post_init__(self):
        self.symbols = [s.upper() for s in self.symbols]
        self.extra_context_symbols = [s.upper() for s in self.extra_context_symbols]

    @classmethod
    def load(cls, path=None, **overrides):
        load_dotenv()
        data = {}
        path = Path(path or ROOT / "config.yaml")
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
        data.update({k: v for k, v in overrides.items() if v is not None})
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def get_env(name, default=None):
    load_dotenv()
    return os.environ.get(name, default)


def alpaca_keys():
    return get_env("ALPACA_API_KEY"), get_env("ALPACA_SECRET_KEY")
