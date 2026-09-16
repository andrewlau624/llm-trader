"""Intra-session trade feedback.

The architecture's point: the model should not have to rediscover, at 13:00, that it has spent
the morning shorting a bull trend. Summarise the day back to it in a form it can act on —
which patterns already failed, how many times, and for how much.
"""

from collections import defaultdict
from datetime import date, datetime

EXIT_ORDER = ("stop", "take_profit", "max_hold", "session_end", "forced", "replay_end")


def _day_of(stamp):
    if stamp is None:
        return None
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp).date()
        except ValueError:
            return None
    if isinstance(stamp, datetime):
        return stamp.date()
    if isinstance(stamp, date):
        return stamp
    return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def trade_feedback(trades, day=None, min_group=2, max_lines=6):
    if not trades:
        return []
    today = [t for t in trades if day is None or _day_of(t.get("closed_at")) == day]
    if not today:
        return []

    lines = []
    pnls = [t.get("pnl", 0.0) for t in today]
    wins = [p for p in pnls if p > 0]
    net = sum(pnls)
    rs = [t.get("r_multiple") for t in today]
    lines.append(
        f"TODAY: {len(today)} closed ({len(wins)}W/{len(today) - len(wins)}L), "
        f"net {net:+,.2f}, avg {_mean(rs):+.2f}R"
    )

    counts = defaultdict(int)
    for t in today:
        counts[t.get("exit_reason", "unknown")] += 1
    parts = [f"{name} x{counts[name]}" for name in EXIT_ORDER if counts.get(name)]
    parts += [f"{k} x{v}" for k, v in counts.items() if k not in EXIT_ORDER]
    lines.append("EXITS: " + ", ".join(parts))

    streak = 0
    for t in reversed(today):
        if t.get("pnl", 0.0) < 0:
            streak += 1
        else:
            break
    if streak >= 2:
        lines.append(
            f"LOSING STREAK: the last {streak} trades all lost. Treat the next setup with "
            f"suspicion and demand cleaner confluence."
        )

    sides = {t.get("side") for t in today}
    if len(sides) == 1 and len(today) >= 2:
        side = sides.pop()
        regimes = {t.get("regime") for t in today if t.get("regime")}
        against = regimes and all(r.startswith("trend_") and r.split("_")[1] !=
                                  ("up" if side == "long" else "down") for r in regimes)
        tag = " (every one of them against the prevailing trend)" if against else ""
        lines.append(
            f"DIRECTIONAL BIAS: all {len(today)} trades today were {side.upper()}{tag}."
        )

    groups = defaultdict(list)
    for t in today:
        groups[(t.get("side"), t.get("regime") or "unknown")].append(t)
    for (side, regime), group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(group) < min_group:
            continue
        gnet = sum(t.get("pnl", 0.0) for t in group)
        if gnet >= 0:
            continue
        gwins = sum(1 for t in group if t.get("pnl", 0.0) > 0)
        lines.append(
            f"REPEATED FAILURE: {len(group)} {side.upper()} entries in a {regime} regime, "
            f"{gwins} winners, net {gnet:+,.2f}. This exact pattern has already not worked today."
        )

    hold = counts_reason(today, "max_hold")
    if hold and hold * 2 >= len(today):
        lines.append(
            f"TIME STOPS DOMINATE: {hold} of {len(today)} exits were max_hold, not target. "
            f"The targets may be set beyond what this market is delivering."
        )
    return lines[:max_lines]


def counts_reason(trades, reason):
    return sum(1 for t in trades if t.get("exit_reason") == reason)
