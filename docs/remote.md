# Running on a server

**Yes, and this is the better way to do it.** The deterministic strategy has no model in the loop,
so there is nothing to host but Python. A $5/month VPS is plenty. No GPU, no Ollama, no API tokens.

(Versions of this project that ran the LLM path needed a model server. That constraint disappeared
when the evidence said to take the model out of the entry decision.)

## What it needs

| | |
|---|---|
| CPU | 1 vCPU. One decision cycle reads ~1,200 bars per symbol across 4 symbols and takes well under a second. |
| RAM | 1 GB works, 2 GB comfortable. pandas plus a few thousand bars. |
| Disk | 2 GB including the venv. |
| Cost | $5-12/month |
| Network | outbound HTTPS to Alpaca. |

## Setup

```bash
sudo adduser --disabled-password --gecos "" trader
sudo -iu trader
git clone https://github.com/andrewlau624/llm-trader /opt/llm-trader
cd /opt/llm-trader

make setup     # venv + deps, creates .env from the example
make env       # opens .env in nano: paste your Alpaca PAPER keys, save, exit
make check     # verifies keys, data, priors and the risk budget
make priors    # builds the measured priors table (once, ~5 minutes)
```

Set the server-side config first, because **yfinance gets rate-limited from datacenter IPs**.
Alpaca's free IEX feed was measured against yfinance across a full session and agreed on 35 of 36
candidate directions with near-identical candidate counts, so the volume ratios the strategy
depends on survive the narrower tape.

```yaml
# config.yaml
data_source: alpaca
strategy: deterministic
basket: [SPY, QQQ, IWM, TQQQ]
```

## Run it as a service

```bash
make service          # installs the unit, enables it, starts it
make service-logs     # follows the log
make service-status
make service-stop
make uninstall        # removes it entirely
```

`make service` fills in the clone path and your username from `deploy/llm-trader.service.in`, so it
works wherever you cloned it. It reads `.env` from the clone (the `-` in `EnvironmentFile` means it
starts even if that file is missing). If you would rather the keys live outside the repo for
permissions reasons, edit `deploy/llm-trader.service.in` to point at `/etc/llm-trader.env` before
installing.

`-u` in the ExecStart is required: without it Python buffers stdout and the log stays empty for
hours.

`make week` also works on Linux — it uses `caffeinate` only if present, since that is macOS-only.
On a server, prefer `make service`: systemd handles restarts natively and survives reboots, which
`supervise.sh` does not.

## Data source: pick deliberately

`make check` reports whichever feed `data_source` names. The two differ in ways that matter here:

| | yfinance | alpaca (IEX) |
|---|---|---|
| history | premarket included, 3 sessions of 1m | RTH only |
| volume | full consolidated tape | ~2-3% of the tape |
| from a datacenter IP | rate-limited or blocked | fine |
| matches the backtest | yes, the priors were built on it | 35/36 direction agreement |

The priors and the 60-session backtest were built on **yfinance** data, so on a residential or home
server leaving `data_source: yfinance` is the more consistent choice. On a hosted VPS, switch to
`alpaca` — being blocked mid-week is the worse problem, and the measured direction agreement says
the strategy survives the narrower tape.

### Why a server is the right place for this

- **Brackets live on Alpaca's side.** If the process dies mid-position, the stop and target still
  exist. The sim broker keeps positions in memory and would lose them on a restart.
- **`Restart=always` covers crashes**, which is the failure mode a VPS actually has. A laptop has
  sleep, lid close and battery instead, and none of those are fixable in code.
- **The daily risk state is persisted** to `state/book.json` (`StateDirectory` above), so a restart
  mid-session does not reset `trades_today` or `day_start_equity` — which would otherwise silently
  disable the daily loss halt.

## Watching it

```bash
python scripts/status.py        # running? decided? holding?
python scripts/week.py          # execution cost and realized edge, aggregated
tail -f /var/log/llm-trader.log
```

## Before pointing real money at it

The measured edge is roughly 15.6 bps per trade with break-even near 16 bps round trip, and the
whole question is what live fills actually cost. That is what `scripts/week.py` measures, from the
`fill_vs_signal` events the broker writes. Run `python scripts/economics.py --equity <n>` too: at
small account sizes the whole-share requirement and the notional cap dominate everything else.
