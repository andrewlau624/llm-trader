"""The one place that decides what to trade.

The backtest and the live loop both call this, so a result measured in the scanner is a statement
about the code that actually runs. Any logic that lives in two places will eventually disagree, and
the disagreement will favour whichever copy you are not looking at.
"""

from .context import build_context
from .data.base import to_utc
from .reversal import evaluate
from .scorer import score_context

DEFAULT_TAIL = 1500


def candidates_for(bars_by_symbol, now, cfg, priors, timeframes, tail=DEFAULT_TAIL):
    """Every symbol that currently shows a valid reversal setup, with its context."""
    picks = []
    for symbol, bars in bars_by_symbol.items():
        if not bars:
            continue
        cutoff = to_utc(now)
        window = [b for b in bars if to_utc(b.ts) <= cutoff][-tail:]
        if len(window) < 60:
            continue
        try:
            ctx = build_context(symbol, window, now, timeframes=timeframes)
        except Exception:
            continue
        if ctx.session is None or ctx.price is None:
            continue
        scored = score_context(ctx, getattr(ctx, "frames", None))
        cand = evaluate(ctx, scored, priors=priors, cfg=cfg)
        if cand is not None:
            picks.append((cand, ctx))
    return picks


def best_candidate(bars_by_symbol, now, cfg, priors, timeframes, tail=DEFAULT_TAIL):
    """Highest expected value first. Returns (candidate, context) or (None, None).

    Ranking by expected bps weighted by conviction, not by conviction alone: a weak score on a
    volatile instrument can still carry more measured expectation than a strong score on a quiet
    one, which is exactly what the instrument comparison showed.
    """
    picks = candidates_for(bars_by_symbol, now, cfg, priors, timeframes, tail=tail)
    if not picks:
        return None, None
    picks.sort(key=lambda pair: -(pair[0].expected_bps * pair[0].score))
    return picks[0]
