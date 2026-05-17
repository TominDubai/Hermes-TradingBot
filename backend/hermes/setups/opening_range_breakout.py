"""
Opening Range Breakout (ORB) — Intraday setup.

Logic (5-minute bars, UTC DatetimeIndex):
  - Define the opening range as the high/low of the first 6 bars after
    the regular-session open (09:30 ET = 13:30 UTC EDT / 14:30 UTC EST).
  - Fire when close > ORB high, volume > 1.5x ORB-bar average,
    price above VWAP, ADX > 20, and current time is before 12:00 ET.
"""
from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import adx as calc_adx
from hermes.indicators.core import vwap as calc_vwap
from hermes.setups.base import Setup, SetupResult

# Number of 5-min bars that make up the opening range (30 min)
_ORB_BARS: int = 6

# UTC hours that correspond to the 09:30 ET session open.
# EDT (summer): 13:30 UTC   |   EST (winter): 14:30 UTC
_SESSION_OPEN_UTC_HOURS_MINS: tuple[tuple[int, int], ...] = (
    (13, 30),  # EDT
    (14, 30),  # EST
)

# Before 12:00 ET only (EDT=16:00 UTC, EST=17:00 UTC)
_NOON_ET_UTC_HOURS: tuple[int, ...] = (16, 17)


class OpeningRangeBreakout(Setup):
    """Bullish breakout above the first 30-minute opening range."""

    name = "opening_range_breakout"
    portfolio = Portfolio.INTRA
    min_bars = 20
    orb_minutes: int = 30

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_session_start(df: pd.DataFrame) -> int | None:
        """
        Search the last 50 bars for a bar whose UTC timestamp matches
        either the EDT or EST session open (09:30 ET).

        Returns the integer positional index inside df, or None.
        """
        search_slice = df.iloc[-50:] if len(df) >= 50 else df
        for pos, ts in zip(range(len(search_slice)), search_slice.index, strict=False):
            for h, m in _SESSION_OPEN_UTC_HOURS_MINS:
                if ts.hour == h and ts.minute == m:
                    # Convert slice-relative position to df-absolute position
                    abs_start = len(df) - len(search_slice)
                    return abs_start + pos
        return None

    # ------------------------------------------------------------------
    # Setup detection
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # noqa: PLR0911
        if not self.validate(df):
            return None

        # ── 1. Find session open bar ──────────────────────────────────
        session_start_idx = self._find_session_start(df)
        if session_start_idx is None:
            return None

        # We need at least 6 bars for the opening range
        orb_end_idx = session_start_idx + _ORB_BARS
        if orb_end_idx > len(df):
            return None  # not enough bars to form the range yet

        # ── 2. Opening range definition ───────────────────────────────
        orb_bars = df.iloc[session_start_idx:orb_end_idx]
        orb_high = orb_bars["high"].max()
        orb_low = orb_bars["low"].min()
        orb_height = orb_high - orb_low
        if orb_height <= 0:
            return None

        orb_avg_volume = orb_bars["volume"].mean()

        # ── 3. Current bar ────────────────────────────────────────────
        current = df.iloc[-1]
        current_ts: pd.Timestamp = df.index[-1]  # type: ignore[assignment]

        # ── 4. Time filter: must be before 12:00 ET ───────────────────
        # EDT noon = 16:00 UTC, EST noon = 17:00 UTC
        current_hour_utc = current_ts.hour
        # Allow up to (but not including) the respective noon UTC hour
        before_noon = current_hour_utc < 16 or (
            current_hour_utc == 16 and current_ts.minute == 0
        )
        # In EST window the noon cutoff shifts to 17:00 UTC
        # We also accept 16:xx when we detected the session at 14:30 (EST)
        # Simple heuristic: if session was detected at hour 14, noon = 17:00 UTC
        detected_hour = df.index[session_start_idx].hour  # type: ignore[index]
        if detected_hour == 14:
            before_noon = current_hour_utc < 17 or (
                current_hour_utc == 17 and current_ts.minute == 0
            )
        if not before_noon:
            return None

        # ── 5. Breakout: close above ORB high ─────────────────────────
        if current["close"] <= orb_high:
            return None

        # ── 6. Volume: > 1.5x ORB average ─────────────────────────────
        if orb_avg_volume <= 0:
            return None
        volume_ratio = current["volume"] / orb_avg_volume
        if volume_ratio < 1.5:
            return None

        # ── 7. VWAP confirmation: price above VWAP ────────────────────
        vwap_series = calc_vwap(df["high"], df["low"], df["close"], df["volume"])
        current_vwap = vwap_series.iloc[-1]
        if pd.isna(current_vwap) or current["close"] <= current_vwap:
            return None

        # ── 8. ADX > 20 ───────────────────────────────────────────────
        adx_series, plus_di, minus_di = calc_adx(df["high"], df["low"], df["close"], 14)
        current_adx = adx_series.iloc[-1]
        if pd.isna(current_adx) or current_adx <= 20:
            return None

        # ── 9. Build result ───────────────────────────────────────────
        entry = float(current["close"])
        stop = float(orb_low)
        target = entry + orb_height * 2.0

        time_of_day = current_ts.strftime("%H:%M UTC")

        return SetupResult(
            score=0.75,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="5m",
            metadata={
                "orb_high": float(orb_high),
                "orb_low": float(orb_low),
                "orb_height": float(orb_height),
                "volume_ratio": float(volume_ratio),
                "vwap": float(current_vwap),
                "adx": float(current_adx),
                "time_of_day": time_of_day,
            },
        )
