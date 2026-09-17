#!/bin/bash
# Restart the live loop if it dies, forever, until stopped.
#
#   nohup caffeinate -s bash scripts/supervise.sh --notional-pct 100 &
#   make stop
#
# caffeinate -s holds off system sleep while on AC power. It does NOT survive closing the lid on a
# laptop: that sleep is a different assertion and the loop will simply pause until you open it.
# If the week has to be unbroken, run it on a desktop or a VPS (see docs/remote.md).
set -u
cd "$(dirname "$0")/.."
LOG="${LLMTRADER_LOG:-/tmp/llmtrader-live.log}"
POWER=$(pmset -g ps 2>/dev/null | head -1)
echo "=== supervisor started $(date) args: $* ===" >> "$LOG"
echo "=== power: $POWER ===" >> "$LOG"
case "$POWER" in
  *"AC Power"*) : ;;
  *) echo "=== WARNING: on battery. caffeinate -s only holds an anti-sleep assertion on AC power," >> "$LOG"
     echo "=== so this run will pause when the machine sleeps. Plug in, or use a VPS." >> "$LOG" ;;
esac
while true; do
  .venv/bin/python -u scripts/live.py --strategy deterministic --broker alpaca "$@" >> "$LOG" 2>&1
  code=$?
  echo "=== live exited with $code at $(date), restarting in 30s ===" >> "$LOG"
  sleep 30
done
