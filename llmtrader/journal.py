import json
import time
from pathlib import Path

from .config import ROOT


def _json_default(o):
    try:
        return o.isoformat()
    except AttributeError:
        return str(o)


class Journal:
    def __init__(self, cfg, run_id=None, symbol="", mode="replay", base_dir=None):
        self.cfg = cfg
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(base_dir or (ROOT / cfg.journal_dir)) / f"{mode}-{self.run_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol
        self.mode = mode
        self.prompt_dir = self.dir / "prompts"
        if cfg.log_raw_responses:
            self.prompt_dir.mkdir(exist_ok=True)
        self._n = 0
        self._mem = {}

    def _append(self, name, record):
        with (self.dir / name).open("a") as fh:
            fh.write(json.dumps(record, default=_json_default) + "\n")
        self._mem.setdefault(name, []).append(record)

    def write_meta(self, meta):
        with (self.dir / "meta.json").open("w") as fh:
            json.dump(meta, fh, indent=2, default=_json_default)

    def log_decision(self, ts, ctx, dashboard, decision, checks, account, extra=None):
        self._n += 1
        rec = {
            "n": self._n,
            "ts": ts,
            "symbol": ctx.symbol,
            "price": ctx.price,
            "regime": ctx.regime,
            "session_phase": ctx.session.phase if ctx.session else None,
            "decision": decision.to_dict(),
            "risk": {"allowed": checks.allowed, "reasons": checks.reasons,
                     "size": checks.size, "risk_dollars": checks.risk_dollars},
            "account": {
                "equity": round(account.equity, 2),
                "day_pnl": round(account.day_pnl, 2),
                "trades_today": account.trades_today,
                "consecutive_losses": account.consecutive_losses,
                "in_position": account.position is not None,
                "halted": account.halted,
            },
        }
        if extra:
            rec.update(extra)
        self._append("decisions.jsonl", rec)
        if self.cfg.log_raw_responses and (dashboard or decision.raw_text):
            stem = f"{self._n:05d}_{time.strftime('%H%M%S')}"
            (self.prompt_dir / f"{stem}.prompt.txt").write_text(dashboard)
            (self.prompt_dir / f"{stem}.raw.txt").write_text(decision.raw_text or "")
        return rec

    def log_trade(self, trade):
        self._append("trades.jsonl", trade.to_dict())

    def log_event(self, event):
        self._append("events.jsonl", event)

    def decisions(self):
        return self._read("decisions.jsonl")

    def trades(self):
        return self._read("trades.jsonl")

    def events(self):
        return self._read("events.jsonl")

    def _read(self, name):
        if name in self._mem:
            return list(self._mem[name])
        path = self.dir / name
        if not path.exists():
            self._mem[name] = []
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._mem[name] = out
        return list(out)


def open_run(path):
    j = Journal.__new__(Journal)
    j.dir = Path(path)
    j.prompt_dir = j.dir / "prompts"
    j.run_id = j.dir.name
    j.symbol = ""
    j.mode = ""
    j.cfg = None
    j._n = 0
    j._mem = {}
    return j


def find_runs(base_dir=None):
    base = Path(base_dir or (ROOT / "runs"))
    if not base.exists():
        return []
    return sorted([p for p in base.iterdir() if p.is_dir() and (p / "decisions.jsonl").exists()])
