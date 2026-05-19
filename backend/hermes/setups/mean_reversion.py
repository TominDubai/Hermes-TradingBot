from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import atr, bollinger_bands, bollinger_pct_b, rsi, sma
from hermes.setups.base import Setup, SetupResult


class MeanReversion(Setup):
    """
    Bollinger lower-band touch + RSI oversold + reclaim.
    Phase 6 tuning: removed MFI requirement, relaxed RSI touch threshold to 40,
    extended touch lookback to 5 bars, removed SMA200 requirement (allows
    stocks in mild downtrends to still trigger).
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

        rsi14 = rsi(close, 14)
        _, _, bb_lower = bollinger_bands(close, 20, 2.0)
        bb_pct = bollinger_pct_b(close, 20, 2.0)
        sma50 = sma(close, 50)
        atr14 = atr(high, low, close, 14)

        c = float(close.iloc[-1])
        rsi_curr = float(rsi14.iloc[-1])
        rsi_prev = float(rsi14.iloc[-2])
        bb_low_curr = float(bb_lower.iloc[-1])
        sma50_val = float(sma50.iloc[-1])
        atr_val = float(atr14.iloc[-1])

        # Price must be above SMA50 (shorter-term uptrend — less strict than SMA200)
        if c <= sma50_val * 0.95:  # allow 5% below SMA50
            return None

        # Check last 5 bars for a lower band touch (extended from 3)
        touch_rsi = None
        touch_bar = None
        for i in range(-5, -1):
            bar_close = float(close.iloc[i])
            bar_bb = float(bb_lower.iloc[i])
            if bar_close <= bar_bb * 1.01:  # within 1% of lower band counts
                touch_rsi = float(rsi14.iloc[i])
                touch_bar = i
                break

        if touch_rsi is None:
            return None

        # RSI at touch: relaxed from 35 to 40
        if touch_rsi >= 40:
            return None

        # Reclaim: last bar closed back above lower band
        if c <= bb_low_curr:
            return None

        # Momentum turning (RSI rising)
        if rsi_curr <= rsi_prev:
            return None

        # Entry / stop / target
        entry = c
        low5 = float(low.iloc[-5:].min())
        stop = low5 - 0.5 * atr_val
        risk = entry - stop
        if risk <= 0:
            return None

        _, bb_mid, _ = bollinger_bands(close, 20, 2.0)
        mid_val = float(bb_mid.iloc[-1])
        target = mid_val
        if (target - entry) / risk < 1.5:
            target = entry + 1.5 * risk

        score = 0.85 if (touch_rsi < 30 and float(bb_pct.iloc[touch_bar]) < -0.1) else 0.70

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
                "distance_to_middle_pct": round((mid_val - c) / c, 4),
                "atr": round(atr_val, 4),
            },
        )
