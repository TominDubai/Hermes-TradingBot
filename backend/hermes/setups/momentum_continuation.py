"""
Momentum Continuation — Intraday setup.

Logic (5-minute bars, UTC DatetimeIndex):
  - Strong initial move: price up > 1 % from session open in the first hour.
  - Shallow pullback: after the move, price retraced 30–50 % (not a full reversal).
  - Continuation: current bar closes above the pullback high.
  - MACD histogram positive and increasing (last bar > previous bar).
  - Volume on continuation bar > 1.3x session average.
  - Price above VWAP throughout the pullback (VWAP held as support).
"""
from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import atr as calc_atr
from hermes.indicators.core import macd as calc_macd
from hermes.indicators.core import vwap as calc_vwap
from hermes.setups.base import Setup, SetupResult

# Session open detection — same as ORB
_SESSION_OPEN_UTC_HOURS_MINS: tuple[tuple[int, int], ...] = (
    (13, 30),  # EDT
    (14, 30),  # EST
)

# First-hour: 12 bars of 5-minute data
_FIRST_HOUR_BARS: int = 12

# Pullback zone: 30–50 % retracement of the initial move
_PULLBACK_MIN: float = 0.30
_PULLBACK_MAX: float = 0.50

# Minimum initial move from session open
_MIN_INITIAL_MOVE_PCT: float = 0.01  # 1 %


class MomentumContinuation(Setup):
    """Continuation after a strong morning move and a shallow pullback."""

    name = "momentum_continuation"
    portfolio = Portfolio.INTRA
    min_bars = 20

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_session_start(df: pd.DataFrame) -> int | None:
        """Return positional index of the session-open bar, searching last 50."""
        search_slice = df.iloc[-50:] if len(df) >= 50 else df
        offset = len(df) - len(search_slice)
        for i, ts in enumerate(search_slice.index):
            for h, m in _SESSION_OPEN_UTC_HOURS_MINS:
                if ts.hour == h and ts.minute == m:
                    return offset + i
        return None

    @staticmethod
    def _session_avg_volume(df: pd.DataFrame) -> float:
        today = df.index[-1].normalize()  # type: ignore[union-attr]
        session = df[df.index.normalize() == today]  # type: ignore[union-attr]
        if len(session) == 0:
            return float(df["volume"].mean())
        return float(session["volume"].mean())

    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # noqa: PLR0911
        if not self.validate(df):
            return None

        # ── Indicators ────────────────────────────────────────────────
        vwap_series = calc_vwap(df["high"], df["low"], df["close"], df["volume"])
        _, _, macd_hist = calc_macd(df["close"])
        atr_series = calc_atr(df["high"], df["low"], df["close"], 14)

        current = df.iloc[-1]
        current_atr = atr_series.iloc[-1]
        current_hist = macd_hist.iloc[-1]
        prev_hist = macd_hist.iloc[-2]

        if any(pd.isna(v) for v in [current_atr, current_hist, prev_hist]):
            return None

        # ── 1. Find session start ─────────────────────────────────────
        session_start_idx = self._find_session_start(df)
        if session_start_idx is None:
            return None

        # Session open price
        session_open = float(df.iloc[session_start_idx]["open"])

        # ── 2. First-hour high ────────────────────────────────────────
        first_hour_end = session_start_idx + _FIRST_HOUR_BARS
        if first_hour_end > len(df) - 1:
            return None  # still in first hour — no pullback possible yet

        first_hour_bars = df.iloc[session_start_idx:first_hour_end]
        first_hour_high = float(first_hour_bars["high"].max())

        # ── 3. Initial move > 1 % ─────────────────────────────────────
        initial_move = first_hour_high - session_open
        initial_move_pct = initial_move / session_open if session_open > 0 else 0.0
        if initial_move_pct < _MIN_INITIAL_MOVE_PCT:
            return None

        # ── 4. Pullback phase: bars after first hour up to current ─────
        pullback_bars = df.iloc[first_hour_end:-1]  # exclude current bar
        if len(pullback_bars) < 2:
            return None

        pullback_low = float(pullback_bars["low"].min())
        pullback_high = float(pullback_bars["high"].max())

        retracement = first_hour_high - pullback_low
        retracement_pct = retracement / initial_move if initial_move > 0 else 0.0

        if not (_PULLBACK_MIN <= retracement_pct <= _PULLBACK_MAX):
            return None

        pullback_depth_pct = float(retracement_pct)

        # ── 5. Continuation: current close above pullback high ─────────
        if current["close"] <= pullback_high:
            return None

        # ── 6. MACD histogram positive AND increasing ──────────────────
        if current_hist <= 0 or current_hist <= prev_hist:
            return None

        # ── 7. Volume > 1.3x session average ──────────────────────────
        session_avg_vol = self._session_avg_volume(df)
        if session_avg_vol <= 0:
            return None
        volume_ratio = current["volume"] / session_avg_vol
        if volume_ratio < 1.3:
            return None

        # ── 8. Price above VWAP throughout pullback ────────────────────
        pullback_vwap = vwap_series.iloc[first_hour_end:-1]
        pullback_close = df["close"].iloc[first_hour_end:-1]
        vwap_held = bool((pullback_close >= pullback_vwap).all())
        if not vwap_held:
            return None

        # ── 9. Build result ───────────────────────────────────────────
        entry = float(current["close"])
        stop = pullback_low - 0.5 * float(current_atr)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + initial_move  # project the same move again

        return SetupResult(
            score=0.72,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="5m",
            metadata={
                "initial_move_pct": round(initial_move_pct * 100, 4),
                "pullback_depth_pct": round(pullback_depth_pct * 100, 4),
                "macd_histogram": float(current_hist),
                "volume_ratio": float(volume_ratio),
                "vwap_held": vwap_held,
            },
        )
