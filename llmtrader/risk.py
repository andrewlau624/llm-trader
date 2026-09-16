from dataclasses import dataclass


@dataclass
class RiskCheck:
    allowed: bool
    reasons: list
    size: float = 0.0
    risk_dollars: float = 0.0

    def summary(self):
        return "; ".join(self.reasons) if self.reasons else "ok"


def _atr_ref(ctx):
    c = ctx.timeframes.get("5m") or ctx.timeframes.get("1m")
    if c is None or c.atr is None:
        return None
    return c.atr


def position_size(cfg, account, entry, stop, multiplier=1.0):
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return 0.0, 0.0
    risk_dollars = account.equity * (cfg.risk_per_trade_pct / 100.0) * multiplier
    size = risk_dollars / risk_per_unit
    notional_cap = account.equity * (cfg.max_notional_pct / 100.0)
    size = min(size, notional_cap / entry)
    return max(0.0, size), risk_dollars


def check(decision, ctx, account, cfg, day=None):
    reasons = []
    if not decision.parse_ok:
        return RiskCheck(False, [f"decision could not be parsed: {decision.parse_error}"])
    if not decision.is_entry:
        return RiskCheck(False, ["hold"])
    if account.halted:
        return RiskCheck(False, [f"account halted: {account.halt_reason}"])
    if account.position is not None:
        return RiskCheck(False, ["already in a position"])
    if account.trades_today >= cfg.max_trades_per_day:
        return RiskCheck(False, [f"max trades per day reached ({cfg.max_trades_per_day})"])
    if decision.action == "enter_short" and not cfg.allow_shorts:
        return RiskCheck(False, ["shorts disabled"])
    if decision.confidence < cfg.min_confidence:
        reasons.append(
            f"confidence {decision.confidence} < minimum {cfg.min_confidence}"
        )
    if (
        account.consecutive_losses >= cfg.max_consecutive_losses
        and decision.confidence < cfg.min_confidence_after_losses
    ):
        reasons.append(
            f"{account.consecutive_losses} consecutive losses: need confidence "
            f">= {cfg.min_confidence_after_losses}"
        )
    if cfg.require_stop and (decision.stop_loss is None or decision.entry is None):
        reasons.append("missing entry or stop loss")
        return RiskCheck(False, reasons)
    entry, stop, target = decision.entry, decision.stop_loss, decision.take_profit
    if entry is None:
        reasons.append("missing entry price")
        return RiskCheck(False, reasons)
    slip = abs(entry - ctx.price) / ctx.price * 100.0
    if slip > cfg.max_entry_slip_pct:
        reasons.append(
            f"entry {entry} is {slip:.3f}% from price {ctx.price} "
            f"(max {cfg.max_entry_slip_pct}%)"
        )
    if decision.action == "enter_long":
        if stop is not None and stop >= entry:
            reasons.append("long stop must be below entry")
        if target is not None and target <= entry:
            reasons.append("long target must be above entry")
    else:
        if stop is not None and stop <= entry:
            reasons.append("short stop must be above entry")
        if target is not None and target >= entry:
            reasons.append("short target must be below entry")
    atr = _atr_ref(ctx)
    if atr and stop is not None:
        dist = abs(entry - stop)
        if dist < 0.5 * atr:
            reasons.append(f"stop {dist:.3f} is tighter than 0.5x ATR(5m) {0.5 * atr:.3f}")
        if dist > 3.0 * atr:
            reasons.append(f"stop {dist:.3f} is wider than 3x ATR(5m) {3.0 * atr:.3f}")
    if cfg.require_regime_alignment:
        fighting = (
            (ctx.regime == "trend_up" and decision.action == "enter_short")
            or (ctx.regime == "trend_down" and decision.action == "enter_long")
        )
        if fighting and decision.confidence < cfg.min_confidence_counter_trend:
            reasons.append(
                f"{decision.action} fights the {ctx.regime} regime: needs confidence "
                f">= {cfg.min_confidence_counter_trend}"
            )
    rr = decision.reward_risk()
    if rr is None:
        reasons.append("could not compute reward:risk")
    elif rr < cfg.min_reward_risk:
        reasons.append(f"reward:risk {rr:.2f} < minimum {cfg.min_reward_risk}")
    if day is not None:
        if ctx.session and ctx.session.minutes_from_open < cfg.no_entry_first_min:
            reasons.append(f"too early in session ({ctx.session.minutes_from_open}m after open)")
        if ctx.session and ctx.session.minutes_to_close < cfg.no_entry_last_min:
            reasons.append(f"too close to close ({ctx.session.minutes_to_close}m left)")
    if reasons:
        return RiskCheck(False, reasons)
    size, risk_dollars = position_size(cfg, account, entry, stop, decision.size_multiplier)
    if size <= 0:
        return RiskCheck(False, ["computed position size is zero"])
    return RiskCheck(True, ["ok"], size=size, risk_dollars=risk_dollars)


def update_halt(account, cfg):
    if account.halted:
        return account
    loss_cap = account.day_start_equity * (cfg.max_daily_loss_pct / 100.0)
    if loss_cap > 0 and account.day_pnl <= -loss_cap:
        account.halted = True
        account.halt_reason = (
            f"daily loss limit hit ({account.day_pnl:+.0f} <= -{loss_cap:.0f})"
        )
    return account
