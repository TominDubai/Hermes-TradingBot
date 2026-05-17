from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import adx, atr, donchian_channel
from hermes.setups.base import Setup, SetupResult


class BreakoutConsolidation(Setup):
    """
    Breakout from a tight consolidation range.
    Fires on daily bars when price escapes a coiling range with expanding volume.
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
        atr_5ago = float(atr14.iloc[-6])
        adx_curr = float(adx14.iloc[-1])
        adx_prev = float(adx14.iloc[-6])  # ADX before breakout

        # Breakout: close above 20-period Donchian upper
        don_up = float(don_upper.iloc[-2])  # prior bar's upper (before today)
        if c <= don_up:
            return None

        # ATR expanding
        if atr_5ago <= 0 or atr_curr <= atr_5ago:
            return None

        # ADX: was low during consolidation, now rising
        if adx_prev >= 20:
            return None
        if adx_curr <= 20:
            return None

        # Volume confirmation
        vol_avg = float(volume.iloc[-20:].mean())
        vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg > 0 else 0.0
        if vol_ratio < 1.3:
            return None

        # Consolidation range: last 20-40 bars have tight range < 8%
        close.iloc[-40:-1]
        range_high = float(high.iloc[-40:-1].max())
        range_low = float(low.iloc[-40:-1].min())
        midpoint = (range_high + range_low) / 2
        range_pct = (range_high - range_low) / midpoint if midpoint > 0 else 99.0
        if range_pct >= 0.08:
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
                "adx_before": round(adx_prev, 2),
                "adx_current": round(adx_curr, 2),
                "volume_ratio": round(vol_ratio, 2),
                "atr_expansion_ratio": round(atr_curr / atr_5ago, 3),
                "donchian_upper": round(don_up, 4),
            },
        )
