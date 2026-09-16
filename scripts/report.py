import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llmtrader.report import (
    report_all,
    report_run,
    summarize_decisions,
    summarize_trades,
)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarise one run or all runs")
    ap.add_argument("run", nargs="?", help="path to a run directory")
    ap.add_argument("--all", action="store_true", help="summarise every run")
    ap.add_argument("--json", action="store_true", help="emit machine readable summary")
    args = ap.parse_args(argv)
    if args.json:
        import json

        from llmtrader.journal import find_runs, open_run

        runs = [Path(args.run)] if args.run else find_runs()
        out = {}
        for r in runs:
            j = open_run(r)
            out[r.name] = {
                "decisions": summarize_decisions(j.decisions()),
                "trades": summarize_trades(j.trades()),
            }
        print(json.dumps(out, indent=2))
        return 0
    if args.run:
        print(report_run(args.run))
    else:
        print(report_all())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
