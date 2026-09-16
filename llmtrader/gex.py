"""Dealer gamma exposure (GEX) overlay.

Gamma exposure tells you what dealers' hedging flow is likely to do to price: positive net gamma
means dealers dampen moves (mean reversion regime), negative means they amplify them (trend
regime). The walls are the strikes where that hedging is concentrated.

Everything here is derived from free, delayed options chains, so two rules matter:
  * IV supplied by free feeds is noisy. Contracts with nonsense IV are dropped, not smoothed.
  * Stale gamma is worse than none: it claims support and resistance at strikes that may have
    moved. A snapshot older than `max_age_min`, or whose spot has drifted, is discarded.
"""

import math
import pickle
import time
from datetime import date, datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
RISK_FREE = 0.04
CONTRACT_MULTIPLIER = 100
MIN_T_YEARS = 1.0 / (365.0 * 24.0)


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_gamma(spot, strike, iv, t_years):
    if spot <= 0 or strike <= 0 or iv <= 0 or t_years <= 0:
        return 0.0
    vol = iv * math.sqrt(t_years)
    if vol <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * iv * iv) * t_years) / vol
    return norm_pdf(d1) / (spot * vol)


def _finite(value):
    """pandas hands back NaN for missing cells, and NaN defeats every comparison it touches,
    so it must be rejected before it reaches the arithmetic."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _usable_iv(iv, lo=0.01, hi=3.0):
    iv = _finite(iv)
    if iv is None or not (lo <= iv <= hi):
        return None
    return iv


def _oi(row):
    oi = _finite(row.get("openInterest"))
    vol = _finite(row.get("volume"))
    if oi is not None and oi > 0:
        return oi
    if vol is not None and vol > 0:
        return vol
    return 0.0


def gamma_profile(spot, rows, t_years, min_oi=100):
    """Per-strike gamma exposure, calls positive and puts negative (dealer convention)."""
    profile = {}
    skipped = 0
    for row in rows:
        strike = _finite(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        iv = _usable_iv(row.get("impliedVolatility"))
        if iv is None:
            skipped += 1
            continue
        oi = _oi(row)
        if oi < min_oi:
            continue
        gamma = bs_gamma(spot, strike, iv, t_years)
        if gamma <= 0:
            continue
        sign = 1.0 if row.get("kind") == "call" else -1.0
        exposure = gamma * oi * CONTRACT_MULTIPLIER * spot * spot * 0.01
        entry = profile.setdefault(strike, {"call": 0.0, "put": 0.0})
        entry["call" if sign > 0 else "put"] += exposure
    return profile, skipped


def _zero_gamma(profile, spot):
    """Spot level where cumulative dealer gamma flips sign. Approximation: walk strikes in
    ascending order, accumulating net exposure, and report where the running sum crosses zero."""
    strikes = sorted(profile)
    if not strikes:
        return None
    running = 0.0
    prev_strike = strikes[0]
    prev_running = 0.0
    for strike in strikes:
        net = profile[strike]["call"] - profile[strike]["put"]
        running += net
        if prev_running == 0.0:
            prev_running = running
            prev_strike = strike
            continue
        if (prev_running < 0 <= running) or (prev_running > 0 >= running):
            span = running - prev_running
            if span == 0:
                return strike
            frac = -prev_running / span
            return prev_strike + frac * (strike - prev_strike)
        prev_running = running
        prev_strike = strike
    return None


def build_snapshot(symbol, spot, blocks, now=None, min_oi=100):
    """blocks: [(days_to_expiry, call_rows, put_rows, expiry_label), ...]

    Each expiry is gamma-weighted with its own time to expiry, then summed. A 0DTE contract and a
    contract three weeks out have wildly different gamma for the same moneyness, so collapsing
    them onto one T would misstate the walls.
    """
    now = now or datetime.now(timezone.utc)
    profile = {}
    skipped = 0
    used = []
    for days, calls, puts, label in blocks:
        t_years = max(days, MIN_T_YEARS * 365.0) / 365.0
        rows = [dict(r, kind="call") for r in calls] + [dict(r, kind="put") for r in puts]
        block_profile, block_skipped = gamma_profile(spot, rows, t_years, min_oi=min_oi)
        skipped += block_skipped
        if block_profile:
            used.append(label)
        for strike, expo in block_profile.items():
            acc = profile.setdefault(strike, {"call": 0.0, "put": 0.0})
            acc["call"] += expo["call"]
            acc["put"] += expo["put"]
    if not profile:
        return None
    call_expo = sum(v["call"] for v in profile.values())
    put_expo = sum(v["put"] for v in profile.values())
    net = call_expo - put_expo
    scale = max(abs(call_expo), abs(put_expo), 1.0)
    if not math.isfinite(scale) or scale <= 0:
        return None

    above = [s for s in profile if s > spot and profile[s]["call"] > 0]
    below = [s for s in profile if s < spot and profile[s]["put"] > 0]
    call_wall = max(above, key=lambda s: profile[s]["call"]) if above else None
    put_wall = max(below, key=lambda s: profile[s]["put"]) if below else None
    zgamma = _zero_gamma(profile, spot)
    return {
        "symbol": symbol,
        "spot": round(spot, 2),
        "fetched_at": now.isoformat(),
        "expiry": ", ".join(used),
        "expiries_used": len(used),
        "net_gex": net,
        "net_gex_pct": round(net / scale * 100.0, 1),
        "gamma_regime": "positive (dealers dampen moves, mean reversion favoured)" if net > 0
        else "negative (dealers amplify moves, trends favoured)",
        "regime": "POSITIVE" if net > 0 else "NEGATIVE",
        "call_wall": round(call_wall, 2) if call_wall else None,
        "put_wall": round(put_wall, 2) if put_wall else None,
        "zero_gamma": round(zgamma, 2) if zgamma else None,
        "strikes_used": len(profile),
        "contracts_skipped_bad_iv": skipped,
    }


def _cache_path(symbol):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"gex_{symbol}.pkl"


def fetch_gex(symbol="SPY", spot=None, max_expiries=3, min_oi=100, now=None,
              cache_age_s=600, allow_stale=False):
    """Aggregate the front expiries into one snapshot. Returns None when the data is not fresh
    enough to be worth showing."""
    now = now or datetime.now(timezone.utc)
    path = _cache_path(symbol)
    if path.exists() and (allow_stale or not cache_age_s
                          or time.time() - path.stat().st_mtime <= cache_age_s):
        try:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            if cached and cached.get("spot"):
                drift = abs((spot - cached["spot"]) / cached["spot"]) if spot else 0.0
                if drift <= 0.01 or allow_stale:
                    cached["age_min"] = round(
                        (now - datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 60.0,
                        1,
                    )
                    return cached
        except Exception:
            pass
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        expiries = list(ticker.options or [])
        if not expiries:
            return None
        if spot is None:
            hist = ticker.history(period="1d", interval="1m")
            spot = float(hist["Close"].iloc[-1]) if len(hist) else None
        if not spot:
            return None
        today = now.astimezone(timezone.utc).date()
        blocks = []
        for expiry in expiries[:max_expiries]:
            try:
                exp_date = date.fromisoformat(expiry)
            except ValueError:
                continue
            days = max((exp_date - today).days, 0) + 0.5
            try:
                chain = ticker.option_chain(expiry)
            except Exception:
                continue
            calls = chain.calls.to_dict("records") if chain.calls is not None else []
            puts = chain.puts.to_dict("records") if chain.puts is not None else []
            if calls or puts:
                blocks.append((days, calls, puts, expiry))
        if not blocks:
            return None
        snapshot = build_snapshot(symbol, spot, blocks, now=now, min_oi=min_oi)
        if snapshot is None:
            return None
        snapshot["age_min"] = 0.0
        with path.open("wb") as fh:
            pickle.dump(snapshot, fh)
        return snapshot
    except Exception:
        return None


def render_line(gex, max_age_min=30):
    if not gex:
        return "GEX        not available (no fresh options data, omitted rather than stale)"
    age = gex.get("age_min", 0.0)
    if age is not None and age > max_age_min:
        return (f"GEX        omitted: last snapshot is {age:.0f}m old (limit {max_age_min}m) "
                f"and old gamma claims support that may have moved")
    bits = [
        f"regime {gex.get('regime')} (net {gex.get('net_gex_pct'):+.0f}% of gross gamma)",
        f"{gex.get('gamma_regime')}",
    ]
    line = f"GEX        {bits[0]}\n           {bits[1]}"
    detail = []
    if gex.get("call_wall"):
        detail.append(f"call wall {gex['call_wall']} (resistance)")
    if gex.get("put_wall"):
        detail.append(f"put wall {gex['put_wall']} (support)")
    if gex.get("zero_gamma"):
        detail.append(f"zero gamma {gex['zero_gamma']} (regime flip level)")
    if detail:
        line += "\n           " + " | ".join(detail)
    if gex.get("contracts_skipped_bad_iv"):
        line += f"\n           ({gex['contracts_skipped_bad_iv']} contracts dropped for bad IV)"
    return line
