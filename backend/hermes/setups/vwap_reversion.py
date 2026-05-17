"""
VWAP Reversion — Intraday setup (LONG only).

Logic (5-minute bars, UTC DatetimeIndex):
  - Price dipped ≥ 0.3 % below VWAP in the last 1–3 bars.
  - Current bar close reclaims above VWAP.
  - RSI(14) < 40 during dip, > 45 now (momentum recovering).
  - Volume on reclaim bar > 1.2x session average.
  - Price above EMA(50) — overall trend bullish.
  - Time filter: 10:00–15:00 ET (skip first 30 min and last 30 min).
"""
from __future__ import annotations

import pandas as pd

from hermes.events.types import Direction, Portfolio
from hermes.indicators.core import atr as calc_atr
from hermes.indicators.core import ema as calc_ema
from hermes.indicators.core import rsi as calc_rsi
from hermes.indicators.core import vwap as calc_vwap
from hermes.setups.base import Setup, SetupResult

# ── Time filter constants (UTC) ───────────────────────────────────────────────
# 10:00 ET:  EDT=14:00 UTC, EST=15:00 UTC
# 15:00 ET:  EDT=19:00 UTC, EST=20:00 UTC
# We store both windows as (open_hour, close_hour) pairs.
_WINDOWS_UTC: tuple[tuple[int, int], ...] = (
    (14, 19),  # EDT
    (15, 20),  # EST
)

# How many lookback bars to search for the dip
_DIP_LOOKBACK: int = 3
_DIP_THRESHOLD_PCT: float = 0.003  # 0.3 %


class VWAPReversion(Setup):
    """Mean-reversion long off a VWAP reclaim after a brief dip."""

    name = "vwap_reversion"
    portfolio = Portfolio.INTRA
    min_bars = 20

    # ------------------------------------------------------------------

    @staticmethod
    def _session_avg_volume(df: pd.DataFrame) -> float:
        """Average volume for today's session bars."""
        today = df.index[-1].normalize()  # type: ignore[union-attr]
        session = df[df.index.normalize() == today]  # type: ignore[union-attr]
        if len(session) == 0:
            return float(df["volume"].mean())
        return float(session["volume"].mean())

    @staticmethod
    def _in_time_window(ts: pd.Timestamp) -> bool:
        """Return True if ts falls inside the 10:00–15:00 ET window (UTC)."""
        h = ts.hour
        return any(open_h <= h < close_h for open_h, close_h in _WINDOWS_UTC)

    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame) -> SetupResult | None:  # noqa: PLR0911
        if not self.validate(df):
            return None

        # ── Indicators ────────────────────────────────────────────────
        vwap_series = calc_vwap(df["high"], df["low"], df["close"], df["volume"])
        rsi_series = calc_rsi(df["close"], 14)
        ema50_series = calc_ema(df["close"], 50)
        atr_series = calc_atr(df["high"], df["low"], df["close"], 14)

        current = df.iloc[-1]
        current_ts: pd.Timestamp = df.index[-1]  # type: ignore[assignment]

        current_vwap = vwap_series.iloc[-1]
        current_rsi = rsi_series.iloc[-1]
        current_ema50 = ema50_series.iloc[-1]
        current_atr = atr_series.iloc[-1]

        # Guard NaNs
        if any(pd.isna(v) for v in [current_vwap, current_rsi, current_ema50, current_atr]):
            return None

        # ── 1. Time filter ────────────────────────────────────────────
        if not self._in_time_window(current_ts):
            return None

        # ── 2. Current bar must reclaim VWAP (close > VWAP) ──────────
        if current["close"] <= current_vwap:
            return None

        # ── 3. Price above EMA(50) — overall trend bullish ────────────
        if current["close"] <= current_ema50:
            return None

        # ── 4. RSI now > 45 ───────────────────────────────────────────
        if current_rsi <= 45:
            return None

        # ── 5. Dip check: look back up to 3 bars ─────────────────────
        lookback = df.iloc[-(_DIP_LOOKBACK + 1):-1]  # exclude current bar
        dip_found = False
        dip_depth_pct = 0.0
        rsi_at_dip = float("nan")

        for i in range(len(lookback) - 1, -1, -1):
            bar = lookback.iloc[i]
            bar_vwap = vwap_series.iloc[-(len(lookback) - i + 1)]
            bar_rsi = rsi_series.iloc[-(len(lookback) - i + 1)]

            if pd.isna(bar_vwap) or pd.isna(bar_rsi):
                continue

            depth_pct = (bar_vwap - bar["close"]) / bar_vwap
            if depth_pct >= _DIP_THRESHOLD_PCT and bar_rsi < 40:
                dip_found = True
                dip_depth_pct = float(depth_pct)
                rsi_at_dip = float(bar_rsi)
                break

        if not dip_found:
            return None

        # ── 6. Volume: > 1.2x session average ─────────────────────────
        session_avg_vol = self._session_avg_volume(df)
        if session_avg_vol <= 0:
            return None
        volume_ratio = current["volume"] / session_avg_vol
        if volume_ratio < 1.2:
            return None

        # ── 7. Build result ───────────────────────────────────────────
        entry = float(current["close"])

        # Stop: session low OR VWAP - 1x ATR (whichever is closer to entry)
        today = current_ts.normalize()
        session_bars = df[df.index.normalize() == today]  # type: ignore[union-attr]
        session_low = float(session_bars["low"].min()) if len(session_bars) > 0 else entry - float(current_atr)
        vwap_minus_atr = float(current_vwap) - float(current_atr)
        stop = max(session_low, vwap_minus_atr)  # higher value = closer to entry
        stop = min(stop, entry)  # safety: stop must be below entry

        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + 2.0 * risk

        return SetupResult(
            score=0.68,
            entry=entry,
            stop=stop,
            target=target,
            direction=Direction.LONG,
            setup_name=self.name,
            timeframe="5m",
            metadata={
                "vwap": float(current_vwap),
                "dip_depth_pct": round(dip_depth_pct * 100, 4),
                "rsi_at_dip": rsi_at_dip,
                "rsi_current": float(current_rsi),
                "ema50": float(current_ema50),
                "volume_ratio": float(volume_ratio),
            },
        )
