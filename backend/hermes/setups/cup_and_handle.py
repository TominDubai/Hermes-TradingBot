from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import atr, rsi
from hermes.setups.base import Setup, SetupResult


class CupAndHandle(Setup):
    """
    Classic cup-and-handle pattern on daily bars.
    Phase 6 tuning: cup depth relaxed to 10% (from 15%), handle depth
    widened to 3-20% (from 5-15%), volume threshold lowered to 1.2x,
    RSI range widened to 45-80.
    """

    name = "cup_and_handle"
    portfolio = Portfolio.MID
    min_bars = 120

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # type: ignore[override]
        if not self.validate(df):
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi14 = rsi(close, 14)
        atr14 = atr(high, low, close, 14)

        # Left rim: highest close in bars[-120:-60]
        left_window = close.iloc[-120:-60]
        left_rim_idx = int(left_window.values.argmax())  # type: ignore[union-attr]
        left_rim_price = float(left_window.iloc[left_rim_idx])

        # Cup bottom: lowest close between left rim area and right rim area
        cup_window = close.iloc[-90:-20]
        cup_bottom = float(cup_window.min())

        # Cup depth: relaxed from 15% to 10%
        cup_depth = left_rim_price - cup_bottom
        cup_depth_pct = cup_depth / left_rim_price
        if cup_depth_pct < 0.10:
            return None

        # Right rim: close within 5% of left rim in last 20 bars (relaxed from 3%)
        right_window = close.iloc[-20:]
        right_rim_prices = right_window[right_window >= left_rim_price * 0.95]
        if right_rim_prices.empty:
            return None
        right_rim_price = float(right_rim_prices.iloc[0])

        # Handle: shallow pullback 3-20% (widened from 5-15%)
        handle_window = close.iloc[-15:]
        handle_low = float(handle_window.min())
        handle_depth_pct = (right_rim_price - handle_low) / right_rim_price
        if not (0.03 <= handle_depth_pct <= 0.20):
            return None

        # Breakout: last bar close > right rim high
        c = float(close.iloc[-1])
        right_high = float(high.iloc[-20:].max())
        if c <= right_high * 0.999:
            return None

        # Volume: lowered to 1.2x (from 1.5x)
        vol_avg = float(volume.iloc[-20:].mean())
        vol_ratio = float(volume.iloc[-1]) / vol_avg if vol_avg > 0 else 0.0
        if vol_ratio < 1.2:
            return None

        # RSI: widened to 45-80 (from 50-75)
        rsi_val = float(rsi14.iloc[-1])
        if not (45 <= rsi_val <= 80):
            return None

        # Entry / stop / target
        entry = c
        handle_low_val = float(low.iloc[-15:].min())
        atr_val = float(atr14.iloc[-1])
        stop = handle_low_val - 0.5 * atr_val
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + cup_depth

        return SetupResult(
            score=0.75,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="1d",
            metadata={
                "cup_depth_pct": round(cup_depth_pct, 4),
                "handle_depth_pct": round(handle_depth_pct, 4),
                "left_rim_price": round(left_rim_price, 4),
                "right_rim_price": round(right_rim_price, 4),
                "volume_ratio": round(vol_ratio, 2),
                "rsi": round(rsi_val, 2),
            },
        )
