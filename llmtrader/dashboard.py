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


def _scored_block(scored):
    lines = []
    lines.append("--- MARKET CONTEXT SCORES ---")
    lines.append(
        f"Trend      {scored.trend_label:<12} {scored.trend:+6.1f} / 100"
        f"   ({scored.trend_note})"
    )
    lines.append(
        f"Momentum   {scored.momentum_label:<12} {scored.momentum:+6.1f} / 100"
        f"   ({scored.momentum_note})"
    )
    snap = (
        f"price is stretched and the reversion pressure points {scored.mr_snap}"
        if scored.mr_snap != "FLAT"
        else "price is not stretched in either direction"
    )
    lines.append(
        f"MeanRev    {scored.mr_pressure:5.0f} / 100        snap {scored.mr_snap:<5} ({snap})"
    )
    if scored.mr_note:
        lines.append(f"           drivers: {scored.mr_note}")
    lines.append(
        f"Volatility {scored.volatility:<12}                    ({scored.volatility_note})"
    )
    lines.append(
        f"Volume     {scored.volume_ratio:.2f}x avg        {scored.volume_label}"
        "   (session-aware, same time of day, IQR-cleaned)"
    )
    if scored.sr_level:
        lines.append(
            f"Nearest S/R {scored.sr_level} at {_f(scored.sr_price)}"
            f" ({_f(scored.sr_distance_atr, 2)}x ATR(5m) away)"
        )
    if scored.gex:
        g = scored.gex
        lines.append(
            f"GEX        {g.get('regime', 'n/a')} | call wall {g.get('call_wall')}"
            f" | put wall {g.get('put_wall')} | zero gamma {g.get('zero_gamma')}"
        )
    else:
        lines.append("GEX        no fresh options data, omitted rather than stale")
    lines.append("")
    lines.append("Score guide: Trend and Momentum are -100 (max bearish) to +100 (max bullish).")
    lines.append("MeanRev is 0-100 (how stretched price is from its mean) and always reports the")
    lines.append("direction a snap-back would take. Volatility and Volume are labels, not numbers.")
    lines.append("High MeanRev against a strong Trend is the classic conflict: decide which wins.")
    return lines


def render_dashboard(ctx: MarketContext, account=None, recent=None, cfg=None, scored=None,
                     feedback=None):
    cfg = cfg or Config()
    s = ctx.session
    scored = scored if scored is not None else getattr(ctx, "scored", None)
    lines = []
    lines.append(f"=== MARKET DASHBOARD :: {ctx.symbol} ===")
    et = ctx.now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    if s:
        lines.append(
            f"Time  {et:%Y-%m-%d %H:%M} ET | {s.phase} | {s.minutes_from_open}m after open | "
            f"{s.minutes_to_close}m to close | price {_f(ctx.price)} | {ctx.regime.upper()}"
        )
    else:
        lines.append(f"Time  {et:%Y-%m-%d %H:%M} ET | price {_f(ctx.price)}")

    if scored is not None:
        lines.append("")
        lines.extend(_scored_block(scored))
        for n in ctx.regime_notes:
            if n.startswith("DATA WARNING"):
                lines.append(f"  {n}")
    else:
        lines.append(f"Regime    : {ctx.regime.upper()}")
        for n in ctx.regime_notes:
            lines.append(f"  - {n}")

    lines.append("")
    lines.append("--- TIMEFRAME MATRIX (last 15 completed bars each) ---")
    lines.append(
        "TF   Chg15b   EMAstack    Slope%/bar    R2   ADX  +DI/-DI   RSI  StochK  MACDhist"
        "   ATR%   %B   BWidth     Z  RVol  Structure   ROC5"
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
    lines.append("Z = zscore of close vs 20-bar mean | RVol = bar volume vs 20-bar avg |")
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
    if scored is not None and scored.sr_distance_atr is not None:
        lines.append(
            f"  nearest structure: {scored.sr_level} at {_f(scored.sr_price)}"
            f" ({_f(scored.sr_distance_atr, 2)}x ATR(5m) away, round numbers excluded)"
        )

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
            f"equity {account.equity:,.2f} | day pnl {account.day_pnl:+,.2f} "
            f"({_signed(account.day_return_pct(), 2, '%')}, measured from the account balance) "
            f"| trades today {account.trades_today}/{cfg.max_trades_per_day}"
        )
        lines.append(
            f"open position: {pos_txt} | consecutive losses {account.consecutive_losses} "
            f"| halted: {'YES - ' + account.halt_reason if account.halted else 'no'}"
        )

    if feedback:
        lines.append("")
        lines.append("--- YOUR RESULTS TODAY ---")
        for line in feedback:
            lines.append(f"  {line}")

    if recent:
        lines.append("")
        lines.append("--- RECENT DECISIONS (most recent last) ---")
        for r in recent[-8:]:
            lines.append("  " + r)

    lines.append("")
    lines.append("--- YOUR JOB ---")
    lines.append(
        "Decide whether to enter a trade NOW or do nothing. Most of the time the correct"
        " answer is hold. Only enter when multiple independent dimensions agree, and make the"
        " thesis you write match the direction you choose."
    )
    return "\n".join(lines)
