"""Deterministic short-horizon reversal signal.

The instrument comparison showed one robust effect across nine instruments and three asset
classes: when price is stretched from its mean, it tends to snap back, and the trend labels
resolve contrarian. That is a formula, not a judgement call.

So it is computed here, in code, with a measured expected value attached. An LLM adds nothing to
this arithmetic and can only damage it by overriding it with a narrative. The LLM's comparative
advantage is unstructured text — headlines, filings, statements — not RSI tables, so the model is
kept for that and removed from this path.

Expected value comes from the signal study's measured excess per trade, scaled by the instrument's
volatility relative to the benchmark it was measured on. Volatility scaling is why the effect looks
three times larger on a 3x ETF and means nothing: the risk scaled with it.
"""

from dataclasses import dataclass, field

BENCHMARK_ATR_PCT = 0.17


@dataclass
class ReversalCandidate:
    symbol: str
    direction: str
    score: float
    expected_bps: float
    components: list = field(default_factory=list)
    entry: float = 0.0
    stop: float = 0.0
    take_profit: float = 0.0
    reason: str = ""

    @property
    def side(self):
        return "long" if self.direction == "long" else "short"

    def reward_risk(self):
        risk = abs(self.entry - self.stop)
        return abs(self.take_profit - self.entry) / risk if risk else 0.0

    def to_dict(self):
        d = dict(self.__dict__)
        d["side"] = self.side
        d["reward_risk"] = round(self.reward_risk(), 2)
        return d


def volatility_scale(ctx):
    """How much bigger this instrument's moves are than the one the priors were measured on."""
    for tf in ("5m", "1m", "1h"):
        c = ctx.timeframes.get(tf)
        if c is not None and c.atr_pct:
            return max(0.25, min(4.0, c.atr_pct / BENCHMARK_ATR_PCT)), c.atr, c.atr_pct
    return 1.0, None, None


def evaluate(ctx, scored, priors=None, cfg=None, min_score=None, stop_atr=None, target_atr=None,
             min_expected_bps=None):
    """Return a candidate when the reversal setup is present, else None.

    Deliberately refuses to manufacture a candidate: the study's central lesson is that most
    windows have no edge, so returning None is the normal outcome, not a failure.
    """
    if min_score is None:
        min_score = getattr(cfg, "reversal_min_score", 60.0)
    if stop_atr is None:
        stop_atr = getattr(cfg, "reversal_stop_atr", 1.0)
    if target_atr is None:
        target_atr = getattr(cfg, "reversal_target_atr", 2.0)
    if min_expected_bps is None:
        min_expected_bps = getattr(cfg, "reversal_min_expected_bps", 3.0)
    if ctx.session is None or ctx.price is None:
        return None
    if scored is None:
        return None
    scale, atr, _atr_pct = volatility_scale(ctx)
    if not atr:
        return None

    components = []
    score = 0.0

    if scored.mr_pressure >= min_score and scored.mr_snap in ("UP", "DOWN"):
        score = scored.mr_pressure
        components.append(
            f"mean-reversion pressure {scored.mr_pressure:.0f}/100 pointing {scored.mr_snap}"
        )
    else:
        return None

    direction = "long" if scored.mr_snap == "UP" else "short"

    if scored.volume_label in ("DEAD", "BELOW_AVG"):
        score -= 15.0
        components.append(f"volume {scored.volume_ratio:.2f}x is too thin to trust the snap")
    elif scored.volume_label in ("ABOVE_AVG", "SURGE"):
        score += 5.0
        components.append(f"volume {scored.volume_ratio:.2f}x confirms participation")

    if scored.volatility == "LOW":
        score -= 10.0
        components.append("volatility LOW, measured as the weakest bucket")
    elif scored.volatility == "HIGH":
        score += 5.0
        components.append("volatility HIGH, measured as the strongest bucket")

    if ctx.session.minutes_from_open < (cfg.no_entry_first_min if cfg else 5):
        return None
    if ctx.session.minutes_to_close < (cfg.no_entry_last_min if cfg else 10):
        return None

    if score < min_score:
        return None

    base_bps = 3.0
    if priors:
        for rule in priors.get("rules", []):
            if "MR>70" in rule.get("name", "") and direction == (
                "short" if "DOWN" in rule["name"] else "long"
            ):
                base_bps = max(base_bps, rule.get("excess_bps", 0.0))
    expected_bps = base_bps * scale

    if expected_bps < min_expected_bps:
        return None

    entry = ctx.price
    if direction == "long":
        stop = entry - stop_atr * atr
        target = entry + target_atr * atr
    else:
        stop = entry + stop_atr * atr
        target = entry - target_atr * atr

    reason = (
        f"{direction.upper()} reversal: " + "; ".join(components)
        + f". Measured expectation {expected_bps:.1f} bps on this instrument's volatility"
        + f" ({scale:.2f}x the benchmark), against {stop_atr:.1f}x ATR risk."
    )
    return ReversalCandidate(
        symbol=ctx.symbol,
        direction=direction,
        score=round(score, 1),
        expected_bps=round(expected_bps, 2),
        components=components,
        entry=round(entry, 2),
        stop=round(stop, 2),
        take_profit=round(target, 2),
        reason=reason,
    )


def rank(candidates):
    """Best first, by measured expectation weighted by conviction."""
    return sorted(candidates, key=lambda c: -(c.expected_bps * c.score))
