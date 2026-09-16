import glob
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.journal import open_run
from llmtrader.report import summarize_decisions, summarize_trades

ET = ZoneInfo("America/New_York")


def running():
    try:
        out = subprocess.run(
            ["pgrep", "-f", "scripts/live.py"], capture_output=True, text=True
        ).stdout.split()
        return out
    except Exception:
        return []


def latest_run(mode="paper"):
    runs = sorted(glob.glob(f"runs/{mode}-*"), key=lambda p: Path(p).stat().st_mtime)
    return Path(runs[-1]) if runs else None


def main():
    pids = running()
    print()
    if pids:
        et = datetime.now(ET)
        print(f"  LIVE  pid {', '.join(pids)}   now {et:%Y-%m-%d %H:%M} ET")
        if et.weekday() < 5 and (et.hour, et.minute) >= (9, 30) and et.hour < 16:
            print("        market open, deciding every 5 minutes")
        else:
            print("        market closed, sleeping until the next open")
    else:
        print("  not running   start it with: make live")
    run = latest_run()
    if run is None:
        print("  no paper runs yet")
        return 0
    j = open_run(run)
    dec, tr = j.decisions(), j.trades()
    meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
    print(f"\n  run      {run.name}  ({meta.get('broker')} broker, {meta.get('model')})")
    d = summarize_decisions(dec)
    t = summarize_trades(tr)
    if d:
        from collections import Counter

        kinds = Counter(x.get("skipped", "llm_call") for x in dec)
        print(
            f"  windows  {d['decisions']}   llm calls {kinds.get('llm_call', 0)}   "
            f"gated {kinds.get('gate', 0)}   in position {kinds.get('in_position', 0)}"
        )
        print(
            f"  entries  proposed {d['enter_long']}L/{d['enter_short']}S   "
            f"approved {d['approved_entries']}   blocked {d['rejected_by_risk']}   "
            f"parse failures {d['parse_failures']}"
        )
        if dec:
            acct = dec[-1]["account"]
            print(
                f"  account  equity {acct['equity']:,.2f}   day pnl {acct['day_pnl']:+,.2f}   "
                f"trades today {acct['trades_today']}   halted {acct['halted']}"
            )
    if t.get("trades"):
        print(
            f"  trades   {t['trades']} ({t['wins']}W/{t['losses']}L, {t['win_rate']}%)   "
            f"pnl {t['total_pnl']:+,.2f}   total R {t['total_r']:+.2f}   "
            f"max dd {t['max_drawdown']:,.2f}"
        )
        print(f"  exits    {t['exit_reasons']}")
    else:
        print("  trades   none yet")
    if dec:
        print("\n  last decisions (local ticker time):")
        for x in dec[-5:]:
            ts = datetime.fromisoformat(x["ts"]).astimezone(ET)
            label = x.get("skipped") or x["decision"]["action"]
            extra = ""
            if x.get("skipped") == "gate":
                extra = f"  ({(x.get('gate') or [''])[0][:60]})"
            elif x["decision"]["action"] != "hold":
                ok = "APPROVED" if x["risk"]["allowed"] else "REJECTED"
                extra = f"  conf {x['decision']['confidence']} {ok}"
            print(f"    {ts:%m-%d %H:%M}  {label}{extra}")
    if tr:
        print("\n  last closes:")
        for x in tr[-4:]:
            closed = datetime.fromisoformat(x["closed_at"]).astimezone(ET)
            print(
                f"    {closed:%m-%d %H:%M}  {x['side']:<5} {x['exit_reason']:<12} "
                f"pnl {x['pnl']:+,.2f}  {x['r_multiple']:+.2f}R  held {x['hold_min']}m"
            )
    events = j.events()
    if events:
        bad = [e for e in events if e.get("event") not in ("entry_filled", "cancelled_open_orders")]
        bad = [e for e in bad if not str(e.get("event", "")).startswith("exit_")]
        if bad:
            print("\n  events worth reading:")
            for e in bad[-5:]:
                print(f"    {e.get('event')}: {str(e.get('reason') or e.get('error') or '')[:90]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
