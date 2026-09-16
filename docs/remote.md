# Running this on a remote host

Short version: use a $5-12/month VPS, and **do not run the model on it.** Serve the model from an
API instead. A GPU host that can serve a 7B model at usable speed costs 30-100x more per month than
the API tokens this thing actually burns, and the token burn is small.

## Why not local inference on a server

| Option | Monthly | Notes |
|---|---|---|
| CPU-only VPS running qwen2.5:7b | $5-12 | ~5-15 tok/s. A 2,000-token prompt plus 300 tokens out is 40-90s per decision. Fits inside a 5-minute cycle, barely, with no headroom. |
| GPU VPS (T4/A10) for local inference | $200-700 | Fast, and completely unjustifiable at these account sizes. |
| Hosted API (DeepSeek direct) | **$0.01-0.30** | ~22 gated calls/day, ~2,000 tokens in and ~300 out each. |

For reference, the setup that inspired this spent about **$6/day** on tokens. That was a frontier
model at 288 calls/day including the overnight session. With the gate (22 calls) and a cheap open
model, the same work is roughly **$0.01/day**. The token bill is not the constraint; it only looks
like one if you run an expensive model on every 5-minute window around the clock.

## What to change in `config.yaml`

```yaml
data_source: alpaca        # yfinance rate-limits and sometimes blocks datacenter IPs
llm_backend: deepseek      # or opencode-go; not ollama, see above
llm_gate: true             # 22 calls/day instead of 76, no measured loss of signal
symbols: [SPY]
extra_context_symbols: [QQQ, IWM, VIXY]
```

Put the keys in `/etc/llm-trader.env` rather than a repo `.env`, and point the service at it.

## Install as a service

```ini
# /etc/systemd/system/llm-trader.service
[Unit]
Description=llm-trader paper loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=trader
WorkingDirectory=/opt/llm-trader
EnvironmentFile=/etc/llm-trader.env
ExecStart=/opt/llm-trader/.venv/bin/python -u scripts/live.py --broker alpaca --gate --interval 5
Restart=always
RestartSec=30
StandardOutput=append:/var/log/llm-trader.log
StandardError=append:/var/log/llm-trader.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now llm-trader
journalctl -fu llm-trader
```

Notes:

- `-u` is required. Without it, Python buffers stdout and the log stays empty for hours.
- `--broker alpaca` is the right choice on a server precisely because **the bracket orders live on
  Alpaca's side**. If the process dies mid-position, the stop and target still exist. The sim broker
  keeps its position in memory and would lose it on restart.
- Timezone does not matter; the code works in ET internally.

## Checking on it

```bash
python scripts/status.py       # running? what has it decided? what does it hold?
python scripts/report.py       # full stats once there are trades
tail -f /var/log/llm-trader.log
```

## Before you point real money at it

The signal study puts the measured edge at roughly **5 bps per trade**, which on any account size
you are likely to deploy works out to about **0.2% per month** — smaller than the noise of a single
month and smaller than the risk-free rate. Run `python scripts/economics.py --equity <n>` and read
the months-to-significance line before deciding that hosting it is worth the effort.

The honest use of a remote host is to **collect a paper track record for free**. Paper trading costs
nothing but the VPS, and a few months of it is the only thing that can tell you whether the measured
lean survives out of sample.
