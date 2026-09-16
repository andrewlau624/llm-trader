from .config import Config
from .context.engine import MarketContext


def _f(v, n=2, suffix="", na="n/a"):
    if v is None:
        return na
    return f"{v:.{n}f}{suffix}"


def _signed(v, n=2, suffix=""):
    if v is None:
        return "n/a"
    return f"{v:+.{n}f}{suffix}"


def _tf_row(c):
    return (
        f"{c.tf:<4} {_signed(c.net_change_pct, 2, '%'):>8}  "
        f"{c.ema_stack:<7} {_signed(c.slope_pct, 4):>9} {_f(c.trend_r2, 2):>5}  "
        f"{_f(c.adx, 1):>5} {_f(c.plus_di, 0):>4}/{_f(c.minus_di, 0):<4} "
        f"{_f(c.rsi, 1):>5} {_f(c.stoch_k, 0):>6}  {_signed(c.macd_hist, 4):>9}  "
        f"{_f(c.atr_pct, 3):>6} {_f(c.bb_pctb, 2):>6} {_f(c.bb_width_pct, 2):>6} "
        f"{_signed(c.zscore, 2):>6} {_f(c.rel_vol, 1):>5}  {c.structure:<7} "
        f"{_signed(c.roc, 2, '%'):>7}"
    )


def render_dashboard(ctx: MarketContext, account=None, recent=None, cfg=None, notes=None):
    cfg = cfg or Config()
    s = ctx.session
    lines = []
    lines.append(f"=== MARKET DASHBOARD :: {ctx.symbol} ===")
    et = ctx.now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    if s:
        lines.append(
            f"Time      : {et:%Y-%m-%d %H:%M} ET  ({s.phase}, {s.minutes_from_open}m after open, "
            f"{s.minutes_to_close}m to close)"
        )
    else:
        lines.append(f"Time      : {et:%Y-%m-%d %H:%M} ET")
    lines.append(f"Price     : {_f(ctx.price)}")
    lines.append(f"Regime    : {ctx.regime.upper()}")
    for n in ctx.regime_notes:
        lines.append(f"  - {n}")

    lines.append("")
    lines.append("--- TIMEFRAME MATRIX (last 15 completed bars each) ---")
    lines.append(
        "TF   Chg15b   EMAstack    Slope%/bar    R2   ADX  +DI/-DI   RSI  StochK  MACDhist   ATR%   %B   BWidth     Z  RVol  Structure   ROC5"
    )
    for tf in cfg.timeframes:
        c = ctx.timeframes.get(tf)
        if c:
            lines.append(_tf_row(c))
    lines.append("")
    lines.append("Column guide: EMAstack = ema9/21/50 order | Slope%/bar = regression slope of last 15")
    lines.append("closes as % of price | R2 = trend quality 0-1 | ADX >22 = trending, <18 = chop |")
    lines.append("StochK 0-100 | MACDhist = macd-signal | ATR% = 14-bar ATR as % of price |")
    lines.append("%B = bollinger position 0=lower 1=upper | BWidth = bollinger width % of price |")
    lines.append("Z = zscore of close vs 20-bar mean (mean reversion) | RVol = bar volume vs 20-bar avg |")
    lines.append("Structure = hh_hl / lh_ll / range | ROC5 = 5-bar rate of change %")

    if s:
        lines.append("")
        lines.append("--- SESSION ---")
        lines.append(
            f"open {_f(s.session_open)} | high {_f(s.session_high)} | low {_f(s.session_low)} "
            f"| range {_f(s.session_range)} | position-in-range {_f(s.range_pos_pct, 0, '%')}"
        )
        lines.append(
            f"gap vs prev close {_signed(s.gap_pct, 2, '%')} | vwap {_f(s.vwap)} "
            f"(price {_signed(s.vwap_dist_pct, 2, '%')} vs vwap, "
            f"{'above' if s.above_vwap else 'below'}) | "
            f"prev session {_signed(s.prev_session_change_pct, 2, '%')}"
        )
        lines.append(
            f"prev day: close {_f(s.prev_close)} high {_f(s.prev_high)} low {_f(s.prev_low)}"
        )
        lines.append(
            f"premarket: high {_f(s.premarket_high)} low {_f(s.premarket_low)} | "
            f"volume: {_f(s.rvol, 1)}x normal for this time of day"
        )

    lines.append("")
    lines.append("--- KEY LEVELS (distance from current price) ---")
    parts = []
    for name, lvl in ctx.key_levels:
        dist = (ctx.price - lvl) / lvl * 100.0 if lvl else 0.0
        parts.append(f"{name} {_f(lvl)} ({_signed(dist, 2, '%')})")
    for i in range(0, len(parts), 3):
        lines.append("  " + " | ".join(parts[i : i + 3]))

    if ctx.cross_market:
        lines.append("")
        lines.append("--- CROSS MARKET ---")
        items = [f"{k} {_signed(v, 2, '%')}" for k, v in ctx.cross_market.items()]
        lines.append("  " + " | ".join(items))

    if account is not None:
        lines.append("")
        lines.append("--- ACCOUNT ---")
        pos = account.position
        pos_txt = "none"
        if pos:
            pos_txt = (
                f"{pos.side.upper()} {pos.qty:g} @ {_f(pos.entry)} stop {_f(pos.stop)} "
                f"target {_f(pos.take_profit)} held "
                f"{int((ctx.now - pos.opened_at).total_seconds() // 60)}m"
            )
        lines.append(
            f"equity {account.equity:,.0f} | day pnl {account.day_pnl:+,.0f} "
            f"({_signed(account.day_return_pct(), 2, '%')}) | "
            f"trades today {account.trades_today}/{cfg.max_trades_per_day}"
        )
        lines.append(
            f"open position: {pos_txt} | consecutive losses {account.consecutive_losses} "
            f"| halted: {'YES - ' + account.halt_reason if account.halted else 'no'}"
        )

    if recent:
        lines.append("")
        lines.append("--- RECENT DECISIONS (most recent last) ---")
        for r in recent[-8:]:
            lines.append("  " + r)

    if notes:
        lines.append("")
        lines.append("--- NOTES ---")
        for n in notes:
            lines.append(f"  - {n}")

    lines.append("")
    lines.append("--- YOUR JOB ---")
    lines.append(
        "Decide whether to enter a trade NOW or do nothing. Most of the time the correct"
        " answer is hold. Only enter when multiple independent signals agree."
    )
    return "\n".join(lines)
