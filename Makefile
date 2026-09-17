# llm-trader. Every command you need is here.
#
#   make            list everything
#   make setup      one-time install on a fresh clone
#   make env        edit your API keys in nano
#   make check      verify it is ready to run
#   make priors     build the measured table the strategy reads
#
#   make week       start the forward paper test (survives crashes)
#   make status     is it running, what has it done
#   make week-report  what the week actually showed
#   make stop       stop it

SHELL    := /bin/bash
APP_DIR  := $(CURDIR)
PY       := $(APP_DIR)/.venv/bin/python
SYSTEM_PY ?= python3
NOTIONAL ?= 100
EDITOR   ?= nano
SERVICE  ?= llm-trader

# parameters for the research targets
SYMBOL   ?= SPY
MODEL    ?= qwen2.5:7b
BACKEND  ?= ollama
DATE     ?=
DAYS     ?= 5
INTERVAL ?= 5
GATE     ?= 1
EQUITY   ?= 1000
SYMBOLS  ?= SPY,QQQ,IWM,TQQQ

.DEFAULT_GOAL := help
.PHONY: help setup venv env check priors test lint \
        week status week-report stop logs once paper live \
        study scan controls mc economics replay backtest report \
        service service-start service-stop service-status service-logs uninstall

help:
	@echo ""
	@echo "  llm-trader"
	@echo "  ==================================================================="
	@echo "  FIRST TIME"
	@echo "    make setup        install dependencies into .venv"
	@echo "    make env          edit API keys in $(EDITOR)"
	@echo "    make check        verify python, keys, data and priors"
	@echo "    make priors       build the measured priors (needs ~5 min, run once)"
	@echo ""
	@echo "  RUN IT"
	@echo "    make week         forward paper test, restarts itself if it dies"
	@echo "    make status       running? what has it decided? what does it hold?"
	@echo "    make week-report  execution cost and realized edge from the paper runs"
	@echo "    make stop         stop everything"
	@echo "    make logs         follow the log"
	@echo ""
	@echo "  SERVER (systemd, needs sudo)"
	@echo "    make service      install + start as a background service"
	@echo "    make service-logs follow the service log"
	@echo "    make service-stop stop the service"
	@echo "    make uninstall    remove the service"
	@echo ""
	@echo "  QUICK TESTS"
	@echo "    make once         one decision cycle right now, then exit"
	@echo "    make paper        live data with simulated fills (no account needed)"
	@echo "    make test         run the unit tests"
	@echo "    make lint         static checks"
	@echo ""
	@echo "  RESEARCH"
	@echo "    make study        do the scored dimensions predict anything?"
	@echo "    make mc           Monte Carlo on the trade sequence"
	@echo "    make scan         deterministic scanner over a basket"
	@echo "    make controls     falsification suite for the simulator"
	@echo "    make economics    what it could make and cost   EQUITY=1000"
	@echo "    make backtest     replay sessions     DAYS=5   DATE=2026-09-16"
	@echo "    make report       summarise every run"
	@echo ""
	@echo "  overrides: SYMBOL=$(SYMBOL) NOTIONAL=$(NOTIONAL) EQUITY=$(EQUITY) EDITOR=$(EDITOR)"
	@echo ""

# ---------------------------------------------------------------- first time

setup: venv
	@if command -v uv >/dev/null 2>&1; then \
		echo "installing dependencies with uv"; \
		uv pip install --python $(PY) -q -r requirements.txt; \
	else \
		echo "installing dependencies with pip"; \
		$(PY) -m pip --version >/dev/null 2>&1 || $(PY) -m ensurepip --upgrade -q; \
		$(PY) -m pip install -q --upgrade pip; \
		$(PY) -m pip install -q -r requirements.txt; \
	fi
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }
	@echo ""
	@echo "  dependencies installed."
	@echo "  next:  make env     (paste your Alpaca PAPER keys)"
	@echo "         make check"
	@echo "         make priors"

venv:
	@if [ ! -x "$(PY)" ]; then \
		if command -v uv >/dev/null 2>&1; then \
			echo "creating .venv with uv"; uv venv --python 3.13 .venv; \
		else \
			echo "creating .venv with $(SYSTEM_PY)"; \
			$(SYSTEM_PY) -m venv .venv; \
		fi; \
	fi

env:
	@test -f .env || cp .env.example .env
	@$(EDITOR) .env
	@echo ""
	@echo "  saved. verifying now..."; @$(PY) scripts/check.py 2>/dev/null | tail -14 || true

check:
	@$(PY) scripts/check.py

priors:
	@echo "building priors from 60 sessions of 5m bars (~5 minutes)..."
	@$(PY) scripts/study.py --granularity 5m --write-priors 2>&1 | tail -4

test:
	@$(PY) -m pytest -q 2>&1 | tail -3

lint:
	@$(PY) -m ruff check .

# ---------------------------------------------------------------- run it

week:
	@$(PY) scripts/preflight.py
	@nohup caffeinate -s bash scripts/supervise.sh --notional-pct $(NOTIONAL) >> /tmp/llmtrader-live.log 2>&1 & echo "  started, restarting itself if it dies"
	@echo "  log:     tail -f /tmp/llmtrader-live.log"
	@echo "  status:  make status"

status:
	@$(PY) scripts/status.py

week-report:
	@$(PY) scripts/week.py

logs:
	@tail -f /tmp/llmtrader-live.log

once:
	@$(PY) scripts/live.py --strategy deterministic --broker alpaca --notional-pct $(NOTIONAL) --once $(if $(filter 1,$(FORCE)),--force,)

paper:
	@$(PY) scripts/live.py --strategy deterministic --broker sim --notional-pct $(NOTIONAL)

stop:
	@pkill -f "scripts/supervise.sh" || true
	@sleep 1
	@pkill -f "scripts/live.py" || true
	@pkill -f "scripts/replay.py" || true
	@pkill -f "caffeinate -s bash scripts/supervise.sh" || true
	@sleep 1
	@if pgrep -f "scripts/live.py" >/dev/null 2>&1; then echo "  STILL RUNNING - check manually"; else echo "  stopped"; fi

# ---------------------------------------------------------------- server

service:
	@sed -e "s|__APP_DIR__|$(APP_DIR)|g" -e "s|__USER__|$$(id -un)|g" \
		deploy/llm-trader.service.in | sudo tee /etc/systemd/system/$(SERVICE).service >/dev/null
	@sudo systemctl daemon-reload
	@sudo systemctl enable --now $(SERVICE)
	@echo "  installed and started.  make service-logs"

service-logs:
	@sudo journalctl -fu $(SERVICE)

service-status:
	@systemctl status $(SERVICE) --no-pager | head -12

service-start:
	@sudo systemctl start $(SERVICE)

service-stop:
	@sudo systemctl stop $(SERVICE)
	@echo "  stopped (make uninstall to remove it entirely)"

uninstall:
	@sudo systemctl disable --now $(SERVICE) 2>/dev/null || true
	@sudo rm -f /etc/systemd/system/$(SERVICE).service
	@sudo systemctl daemon-reload
	@echo "  service removed"

# ---------------------------------------------------------------- research

study:
	@$(PY) scripts/study.py --granularity 5m --write-priors

study-compare:
	@$(PY) scripts/study.py --compare $(SYMBOLS) --granularity 5m

scan:
	@$(PY) scripts/scan.py --symbols $(SYMBOLS) --granularity 5m --notional-pct $(NOTIONAL)

controls:
	@echo "=== real (must be positive) ==="
	@$(PY) scripts/scan.py --symbols $(SYMBOLS) --notional-pct 100 2>&1 | sed -n '5,8p'
	@echo "=== flipped (must LOSE) ==="
	@$(PY) scripts/scan.py --symbols $(SYMBOLS) --notional-pct 100 --control flip 2>&1 | sed -n '5,8p'
	@echo "=== random (must be ~flat) ==="
	@$(PY) scripts/scan.py --symbols $(SYMBOLS) --notional-pct 100 --control random 2>&1 | sed -n '5,8p'

mc:
	@$(PY) scripts/mc.py --paths 20000

economics:
	@$(PY) scripts/economics.py --equity $(EQUITY)

replay:
	@$(PY) scripts/replay.py $(if $(DATE),--date $(DATE),--days $(DAYS)) --symbol $(SYMBOL) \
		--model $(MODEL) --backend $(BACKEND) --interval $(INTERVAL) \
		$(if $(filter 1,$(GATE)),--gate,)

backtest: replay

report:
	@$(PY) scripts/report.py
