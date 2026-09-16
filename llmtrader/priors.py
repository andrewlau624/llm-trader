"""Empirical priors, measured rather than assumed.

The dashboard tells the model what the market looks like. This tells it what those descriptions
have actually been worth on this instrument, measured over the sessions we have. It exists because
the natural reading of a trend score was backwards in the measured sample, and a model that does
not know that will keep making the same confident mistake.

Regenerate with:  python scripts/study.py --granularity 5m --write-priors
"""

import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "empirical_priors.json"


def load_priors(path=None):
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def render_priors(priors, max_lines=14):
    if not priors:
        return []
    lines = ["", "--- EMPIRICAL PRIORS (measured on this instrument, not assumed) ---"]
    lines.append(
        f"Source: {priors.get('windows'):,} {priors.get('symbol')} "
        f"{priors.get('granularity')} windows across {priors.get('sessions')} sessions, "
        f"{priors.get('horizon_min')}m forward EXCESS return, net of "
        f"{priors.get('cost_bps')}bps round-trip cost."
    )
    lines.append("Excess means each session's own average move is subtracted, so a label cannot")
    lines.append("look good merely for being on the right side of the day's drift.")

    labels = priors.get("labels") or {}
    if labels:
        lines.append("")
        lines.append("How the trend label actually resolved (positive = price rose after it):")
        for name in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR"):
            entry = labels.get(name)
            if not entry:
                continue
            lines.append(
                f"  {name:<13} n={entry['n']:>5}  {entry['excess_bps']:+6.2f} bps  "
                f"t={entry['t']:+5.2f}  win {entry['win']:.0f}%"
            )
        lines.append(
            "  In this sample those labels ran CONTRARIAN and monotonic: the more bullish the"
            " label,"
        )
        lines.append(
            "  the worse price did afterwards. Reading the Trend score at face value is a"
            " losing habit here."
        )

    rules = priors.get("rules") or []
    if rules:
        lines.append("")
        lines.append("Measured rules, with both halves of the sample agreeing on sign:")
        for r in rules[:max_lines]:
            h1, h2 = r.get("h1"), r.get("h2")
            halves = f"  halves {h1:+.1f} / {h2:+.1f}" if h1 is not None and h2 is not None else ""
            lines.append(
                f"  {r['name']:<34} {r['excess_bps']:+6.2f} bps  n={r['n']:<4}"
                f" t={r['t']:+5.2f}{halves}"
            )

    lines.append("")
    lines.append("How to use this: as a prior to lean against, not a promise. These windows")
    lines.append("overlap, which inflates the t-statistics by roughly a factor of three, and the")
    lines.append("effect is smaller than the noise of any single trade. It says which way to lean,")
    lines.append("not that a trade will work.")
    return lines
