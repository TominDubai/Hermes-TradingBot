from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import adx, atr, ema, rsi
from hermes.setups.base import Setup, SetupResult


class EMATrendFollow(Setup):
    """
    EMA 50/200 trend-follow with pullback entry.
    Fires on daily bars when price pulls back to the 50 EMA in an uptrend.
    """

    name = "ema_trend_follow"
    portfolio = Portfolio.LONG
    min_bars = 250

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # type: ignore[override]
        if not self.validate(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        e50 = ema(close, 50)
        e200 = ema(close, 200)
        rsi14 = rsi(close, 14)
        atr14 = atr(high, low, close, 14)
        adx14, _, _ = adx(high, low, close, 14)

        # Check last bar values
        last = -1
        c = float(close.iloc[last])
        e50_val = float(e50.iloc[last])
        e200_val = float(e200.iloc[last])
        rsi_val = float(rsi14.iloc[last])
        atr_val = float(atr14.iloc[last])
        adx_val = float(adx14.iloc[last])
        vol_avg = float(volume.iloc[-20:].mean())
        vol_ratio = float(volume.iloc[last]) / vol_avg if vol_avg > 0 else 0.0

        # Conditions
        if c <= e200_val:
            return None
        if e50_val <= e200_val:
            return None
        if not (45 <= rsi_val <= 65):
            return None
        if adx_val <= 20:
            return None
        if vol_ratio < 1.2:
            return None

        # Pullback: EMA50 was within 3% of EMA200 in last 20 bars
        spread_pct = abs(e50.iloc[-20:] - e200.iloc[-20:]) / e200.iloc[-20:]
        if not (spread_pct < 0.03).any():
            return None

        # Entry / stop / target
        entry = c
        stop = e200_val - atr_val
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + 3.0 * risk

        score = 0.85 if (adx_val > 30 and vol_ratio > 1.5) else 0.70

        return SetupResult(
            score=score,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="1d",
            metadata={
                "rsi": round(rsi_val, 2),
                "adx": round(adx_val, 2),
                "ema50": round(e50_val, 4),
                "ema200": round(e200_val, 4),
                "atr": round(atr_val, 4),
                "ema_spread_pct": round(float(spread_pct.min()), 4),
                "volume_ratio": round(vol_ratio, 2),
                "close_vs_ema200_pct": round((c - e200_val) / e200_val, 4),
            },
        )
