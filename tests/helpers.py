from datetime import datetime, timedelta, timezone

from llmtrader.broker import AccountState
from llmtrader.context.engine import MarketContext, SessionContext, TFContext
from llmtrader.data.base import ET, Bar


def make_bars(count=120, start_et=(9, 30), day=(2026, 9, 16), start_price=100.0, step=0.05,
              volume=1000, accel=0.0):
    start = datetime(*day, start_et[0], start_et[1], tzinfo=ET)
    bars = []
    price = start_price
    for i in range(count):
        o = price
        price = price + step + accel * i
        c = price
        bars.append(
            Bar(
                ts=(start + timedelta(minutes=i)).astimezone(timezone.utc),
                open=round(o, 4),
                high=round(max(o, c) + 0.05, 4),
                low=round(min(o, c) - 0.05, 4),
                close=round(c, 4),
                volume=volume + i,
                symbol="TEST",
            )
        )
    return bars


def make_tf(tf="5m", atr=1.0, price=100.0, adx=30.0, rsi=55.0, rel_vol=1.0, zscore=0.0,
            structure="hh_hl", ema_stack="bull", macd_hist=0.01, bb_width_pct=0.3,
            bb_pctb=0.6, slope_pct=0.01, trend_r2=0.6, roc=0.2, plus_di=None,
            minus_di=None, ema50=None):
    if plus_di is None:
        plus_di, minus_di = (12.0, 25.0) if ema_stack == "bear" else (25.0, 12.0)
    if ema50 is None:
        ema50 = price + 0.2 if ema_stack == "bear" else price - 0.2
    return TFContext(
        tf=tf,
        bars=15,
        last=price,
        net_change_pct=0.1,
        ema9=price,
        ema21=price - 0.1,
        ema50=ema50,
        ema_stack=ema_stack,
        slope_pct=slope_pct,
        trend_r2=trend_r2,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        rsi=rsi,
        stoch_k=55.0,
        stoch_d=52.0,
        macd=0.05,
        macd_signal=0.04,
        macd_hist=macd_hist,
        roc=roc,
        bb_pctb=bb_pctb,
        bb_width_pct=bb_width_pct,
        atr=atr,
        atr_pct=atr / price * 100.0,
        zscore=zscore,
        structure=structure,
        rel_vol=rel_vol,
    )


def make_session(price=100.0, minute_open=60, minute_close=200):
    return SessionContext(
        session_open=99.0,
        session_high=101.0,
        session_low=98.0,
        session_range=3.0,
        range_pos_pct=60.0,
        gap_pct=0.1,
        prev_close=98.9,
        prev_high=99.8,
        prev_low=97.5,
        prev_session_change_pct=0.4,
        vwap=99.5,
        vwap_dist_pct=0.5,
        above_vwap=True,
        premarket_high=99.3,
        premarket_low=98.2,
        minutes_from_open=minute_open,
        minutes_to_close=minute_close,
        phase="morning",
        cum_volume=500000,
        expected_cum_volume=480000,
        rvol=1.04,
    )


def make_ctx(price=100.0, atr=1.0, minute_open=60, minute_close=200, regime="trend_up",
             tfs=None, **tf_kwargs):
    tfs = tfs or {
        "1m": make_tf("1m", atr=atr / 2, price=price, **{k: v for k, v in tf_kwargs.items()}),
        "5m": make_tf("5m", atr=atr, price=price, **tf_kwargs),
        "1h": make_tf("1h", atr=atr * 3, price=price, **tf_kwargs),
    }
    return MarketContext(
        symbol="TEST",
        now=datetime(2026, 9, 16, 14, 30, tzinfo=timezone.utc),
        price=price,
        timeframes=tfs,
        session=make_session(price, minute_open, minute_close),
        regime=regime,
        regime_notes=["test"],
        key_levels=[("prev_day_close", 98.9), ("session_vwap", 99.5), ("round_1", 100.0)],
        cross_market={"QQQ 5m": 0.1},
    )


def make_account(equity=100000.0, **kw):
    acct = AccountState(
        equity=equity,
        cash=equity,
        realized_pnl=0.0,
        day_pnl=0.0,
        day_start_equity=equity,
        trades_today=0,
        consecutive_losses=0,
    )
    for k, v in kw.items():
        setattr(acct, k, v)
    return acct
