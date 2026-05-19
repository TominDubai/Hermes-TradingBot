from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import adx, atr, donchian_channel
from hermes.setups.base import Setup, SetupResult


class BreakoutConsolidation(Setup):
    """
    Breakout from a tight consolidation range.
    Phase 6 tuning: relaxed range width to 15% (from 8%), ADX pre-breakout
    threshold raised to 25 (from 20), volume threshold lowered to 1.1x,
    removed ATR expansion requirement, ADX post check lowered to 18.
    """

    name = "breakout_consolidation"
    portfolio = Portfolio.MID
    min_bars = 60

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # type: ignore[override]
        if not self.validate(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        atr14 = atr(high, low, close, 14)
        adx14, _, _ = adx(high, low, close, 14)
        don_upper, _, don_lower = donchian_channel(high, low, 20)

        c = float(close.iloc[-1])
        atr_curr = float(atr14.iloc[-1])
        adx_curr = float(adx14.iloc[-1])

        # Breakout: close above 20-period Donchian upper
        don_up = float(don_upper.iloc[-2])
        if c <= don_up:
            return None

        # ADX showing trend starting (lowered from 20 to 18)
        if adx_curr <= 18:
            return None

        # Volume confirmation (lowered from 1.3x to 1.1x)
        vol_avg = float(volume.iloc[-20:].mean())
        vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg > 0 else 0.0
        if vol_ratio < 1.1:
            return None

        # Consolidation range: last 20-40 bars — relaxed to 15% (from 8%)
        range_high = float(high.iloc[-40:-1].max())
        range_low = float(low.iloc[-40:-1].min())
        midpoint = (range_high + range_low) / 2
        range_pct = (range_high - range_low) / midpoint if midpoint > 0 else 99.0
        if range_pct >= 0.15:
            return None

        # Entry / stop / target
        entry = c
        range_bottom = float(don_lower.iloc[-1])
        stop = range_bottom - 0.5 * atr_curr
        risk = entry - stop
        if risk <= 0:
            return None
        range_height = range_high - range_low
        target = entry + 2.0 * range_height
        if (target - entry) / risk < 1.2:
            target = entry + 1.2 * risk

        return SetupResult(
            score=0.72,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="1d",
            metadata={
                "range_height_pct": round(range_pct, 4),
                "adx_current": round(adx_curr, 2),
                "volume_ratio": round(vol_ratio, 2),
                "donchian_upper": round(don_up, 4),
            },
        )
