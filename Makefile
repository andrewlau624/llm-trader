# llm-trader - one-word commands.
#
#   make              list every command
#   make setup        create the venv and install deps
#   make check        verify python, ollama, model, keys, and today's data
#   make test         run the test suite
#
#   make replay       replay one past session (DATE=YYYY-MM-DD)
#   make backtest     replay the last N sessions (DAYS=5)
#   make report       summarise every run so far
#
#   make paper        paper loop: live data, simulated fills, no account needed
#   make once         one decision cycle right now, then exit
#   make live         paper loop against your Alpaca paper account
#   make stop         stop background runs

SHELL   := /bin/bash
APP_DIR := $(CURDIR)
PY      := $(APP_DIR)/.venv/bin/python
UV      := uv

SYMBOL   ?= SPY
MODEL    ?= qwen2.5:7b
BACKEND  ?= ollama
DATE     ?=
DAYS     ?= 5
INTERVAL ?= 5
GATE     ?= 1
NOTIONAL ?= 100   # notional per trade as % of equity, for `make week`

REPLAY_FLAGS = $(if $(DATE),--date $(DATE),--days $(DAYS)) --symbol $(SYMBOL) \
               --model $(MODEL) --backend $(BACKEND) --interval $(INTERVAL) \
               $(if $(filter 1,$(GATE)),--gate,)

.DEFAULT_GOAL := help
.PHONY: help setup check test lint fmt replay backtest report study study-1m economics scan controls week week-report mc paper once live stop status logs clean

help:
	@echo ""
	@echo "  llm-trader"
	@echo "  ------------------------------------------------------------------"
	@echo "  make setup       create .venv and install dependencies"
	@echo "  make check       verify environment, data and model availability"
	@echo "  make test        run the unit tests"
	@echo "  make lint        static checks (ruff)"
	@echo ""
	@echo "  make replay      replay one session     e.g. make replay DATE=2026-09-16"
	@echo "  make backtest    replay N sessions      e.g. make backtest DAYS=5 GATE=0"
	@echo "  make report      summarise all runs, win rate and R multiples"
	@echo "  make study       measure whether the scored dimensions predict anything"
	@echo "  make economics   what it could make and cost  e.g. make economics EQUITY=5000"
	@echo "  make scan        deterministic reversal scanner across a basket"
	@echo "  make controls    sanity-check the simulator (flip/random/resolution)"
	@echo "  make mc          Monte Carlo over the trade sequence"
	@echo "  make week        start the week-long forward paper test (deterministic)"
	@echo "  make week-report slippage and realized edge from the week just run"
	@echo ""
	@echo "  make paper       paper loop, simulated fills, live data, no account"
	@echo "  make once        one decision cycle now, then exit"
	@echo "  make live        paper loop against Alpaca paper (needs .env keys)"
	@echo "  make status      is it running, what has it done, what does it hold"
	@echo "  make stop        stop any background runs"
	@echo ""
	@echo "  MODEL=$(MODEL)  SYMBOL=$(SYMBOL)  GATE=$(GATE)  DAYS=$(DAYS)"
	@echo "  e.g.  make backtest DAYS=5 MODEL=qwen2.5:7b GATE=1"
	@echo ""

setup:
	@test -d .venv || $(UV) venv --python 3.13 .venv
	$(UV) pip install --python $(PY) -r requirements.txt
	@echo "done. copy .env.example to .env if you want Alpaca paper keys"

check:
	@$(PY) scripts/check.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check . --fix

replay:
	$(PY) scripts/replay.py $(REPLAY_FLAGS) --tag "$(SYMBOL)-$(DATE)$(DAYS)d"

backtest:
	$(PY) scripts/replay.py $(REPLAY_FLAGS) --tag "$(SYMBOL)-$(DAYS)d"

report:
	@$(PY) scripts/report.py

status:
	@$(PY) scripts/status.py

# the week-long forward test: deterministic rule, Alpaca paper, full risk budget
week:
	@$(PY) scripts/preflight.py
	@nohup caffeinate -s bash scripts/supervise.sh --notional-pct $(NOTIONAL) >> /tmp/llmtrader-live.log 2>&1 & echo "started; log: /tmp/llmtrader-live.log"

week-report:
	@$(PY) scripts/week.py

mc:
	$(PY) scripts/mc.py --paths 20000

study:
	$(PY) scripts/study.py --granularity 5m --write-priors

study-1m:
	$(PY) scripts/study.py --granularity 1m

scan:
	$(PY) scripts/scan.py --symbols $(if $(SYMBOLS),$(SYMBOLS),SPY,QQQ,IWM,TQQQ) --granularity 5m

controls:
	@echo "=== real ==="
	@$(PY) scripts/scan.py --symbols $(if $(SYMBOLS),$(SYMBOLS),SPY,QQQ,IWM,TQQQ) 2>&1 | sed -n '3,12p'
	@echo "=== flipped (must LOSE) ==="
	@$(PY) scripts/scan.py --symbols $(if $(SYMBOLS),$(SYMBOLS),SPY,QQQ,IWM,TQQQ) --control flip 2>&1 | sed -n '3,12p'
	@echo "=== random (must be ~flat) ==="
	@$(PY) scripts/scan.py --symbols $(if $(SYMBOLS),$(SYMBOLS),SPY,QQQ,IWM,TQQQ) --control random 2>&1 | sed -n '3,12p'
	@echo "=== 1m resolution (must agree in sign with 5m) ==="
	@$(PY) scripts/scan.py --symbols SPY,QQQ --granularity 1m 2>&1 | sed -n '3,12p'

economics:
	$(PY) scripts/economics.py $(if $(EQUITY),--equity $(EQUITY),--equity 1000)

paper:
	$(PY) scripts/live.py --broker sim --symbol $(SYMBOL) --model $(MODEL) \
		--backend $(BACKEND) --interval $(INTERVAL) $(if $(filter 1,$(GATE)),--gate,)

once:
	$(PY) scripts/live.py --broker sim --symbol $(SYMBOL) --model $(MODEL) \
		--backend $(BACKEND) --once $(if $(filter 1,$(GATE)),--gate,)

live:
	$(PY) scripts/live.py --broker alpaca --symbol $(SYMBOL) --model $(MODEL) \
		--backend $(BACKEND) --interval $(INTERVAL) $(if $(filter 1,$(GATE)),--gate,)

stop:
	@pkill -f "scripts/live.py" || true
	@pkill -f "scripts/replay.py" || true
	@echo "stopped"

logs:
	@ls -t runs | head -1 | xargs -I{} tail -f runs/{}/decisions.jsonl

clean:
	rm -rf runs .pytest_cache data/cache
