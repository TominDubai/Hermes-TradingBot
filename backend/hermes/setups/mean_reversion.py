from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import atr, bollinger_bands, bollinger_pct_b, mfi, rsi, sma
from hermes.setups.base import Setup, SetupResult


class MeanReversion(Setup):
    """
    Bollinger lower-band touch + RSI oversold + reclaim.
    Fires on daily bars in an overall uptrend.
    """

    name = "mean_reversion"
    portfolio = Portfolio.MID
    min_bars = 60

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # type: ignore[override]
        if not self.validate(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi14 = rsi(close, 14)
        _, _, bb_lower = bollinger_bands(close, 20, 2.0)
        bb_pct = bollinger_pct_b(close, 20, 2.0)
        mfi14 = mfi(high, low, close, volume, 14)
        sma200 = sma(close, 200)
        atr14 = atr(high, low, close, 14)

        # Last bar values
        c = float(close.iloc[-1])
        rsi_curr = float(rsi14.iloc[-1])
        rsi_prev = float(rsi14.iloc[-2])
        bb_low_curr = float(bb_lower.iloc[-1])
        sma200_val = float(sma200.iloc[-1])
        atr_val = float(atr14.iloc[-1])

        # Overall uptrend
        if c <= sma200_val:
            return None

        # Check last 3 bars for a lower band touch
        touch_rsi = None
        touch_mfi = None
        for i in range(-3, -1):
            bar_close = float(close.iloc[i])
            bar_bb = float(bb_lower.iloc[i])
            if bar_close <= bar_bb:
                touch_rsi = float(rsi14.iloc[i])
                touch_mfi = float(mfi14.iloc[i])
                break

        if touch_rsi is None:
            return None

        # Touch conditions
        if touch_rsi >= 35:
            return None
        if touch_mfi is not None and touch_mfi >= 40:
            return None

        # Reclaim: last bar closed back above lower band
        if c <= bb_low_curr:
            return None

        # Momentum turning
        if rsi_curr <= rsi_prev:
            return None

        # Entry / stop / target
        entry = c
        low5 = float(low.iloc[-5:].min())
        stop = min(low5, c - atr_val) - 0.5 * atr_val
        risk = entry - stop
        if risk <= 0:
            return None

        _, bb_mid, _ = bollinger_bands(close, 20, 2.0)
        mid_val = float(bb_mid.iloc[-1])
        target = mid_val
        if (target - entry) / risk < 1.5:
            target = entry + 1.5 * risk  # ensure minimum R:R

        score = 0.85 if (touch_rsi < 25 and float(bb_pct.iloc[-3]) < -0.1) else 0.70

        return SetupResult(
            score=score,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="1d",
            metadata={
                "rsi_at_touch": round(touch_rsi, 2),
                "rsi_current": round(rsi_curr, 2),
                "bb_pct_b": round(float(bb_pct.iloc[-1]), 4),
                "mfi": round(touch_mfi, 2) if touch_mfi is not None else None,
                "distance_to_middle_pct": round((mid_val - c) / c, 4),
                "atr": round(atr_val, 4),
            },
        )
