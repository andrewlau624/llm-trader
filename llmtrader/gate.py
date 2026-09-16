def _trigger_and_participation(ctx, cfg):
    triggers = []
    participation = []
    r5 = ctx.timeframes.get("5m")
    r1 = ctx.timeframes.get("1m")
    s = ctx.session
    ref = r5 or r1
    if ref is None:
        return [], []
    if ref.adx is not None and ref.adx >= cfg.gate_min_adx:
        triggers.append(f"5m adx {ref.adx:.0f} >= {cfg.gate_min_adx}")
    if r1 is not None and r1.zscore is not None and abs(r1.zscore) >= cfg.gate_min_abs_zscore:
        triggers.append(f"1m zscore {r1.zscore:+.2f} stretched vs 20-bar mean")
    if ref.structure in ("hh_hl", "lh_ll"):
        triggers.append(f"5m swing structure is {ref.structure}")
    if ref.bb_width_pct is not None and ref.bb_width_pct >= cfg.gate_min_bbwidth:
        triggers.append(f"5m bollinger width {ref.bb_width_pct:.2f}% >= {cfg.gate_min_bbwidth}%")

    if ref.rel_vol is not None and ref.rel_vol >= cfg.gate_min_relvol:
        participation.append(f"5m volume {ref.rel_vol:.1f}x its 20-bar average")
    if s is not None:
        if s.rvol is not None and s.rvol >= cfg.gate_min_session_rvol:
            participation.append(f"session volume {s.rvol:.1f}x normal for this time of day")
        if s.vwap_dist_pct is not None and abs(s.vwap_dist_pct) >= cfg.gate_min_vwap_dist:
            participation.append(f"price {s.vwap_dist_pct:+.2f}% from session vwap")
        if s.range_pos_pct is not None and (s.range_pos_pct <= 10 or s.range_pos_pct >= 90):
            participation.append(f"price at extreme of session range ({s.range_pos_pct:.0f}%)")
    return triggers, participation


def gate_reasons(ctx, cfg):
    triggers, participation = _trigger_and_participation(ctx, cfg)
    return triggers + participation


def should_call_llm(ctx, cfg):
    if not cfg.llm_gate:
        return True, ["gate disabled"]
    triggers, participation = _trigger_and_participation(ctx, cfg)
    if not triggers:
        return False, [
            "no directional trigger: trend is weak (adx below "
            f"{cfg.gate_min_adx}), no stretched z-score, no clean swing structure"
        ]
    if not participation:
        return False, [
            "directional trigger present but nothing is happening: volume at or below "
            "normal and price mid-range near vwap"
        ]
    return True, triggers + participation
