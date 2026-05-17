"""
Feature Extractor — produces a rich, JSON-serialisable feature vector from the
last bar of a DataFrame for every signal that fires.

The returned dict is stored in Signal.features_json and forms the ML training
set (Phase 7 XGBoost pipeline).

All values are Python-native float / int / str / None.
NaN → None so the dict is always JSON-safe.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from hermes.indicators.core import adx as calc_adx
from hermes.indicators.core import atr as calc_atr
from hermes.indicators.core import bollinger_bands, bollinger_pct_b
from hermes.indicators.core import ema as calc_ema
from hermes.indicators.core import macd as calc_macd
from hermes.indicators.core import obv as calc_obv
from hermes.indicators.core import roc as calc_roc
from hermes.indicators.core import rsi as calc_rsi
from hermes.setups.base import SetupResult

_EXPECTED_KEYS: tuple[str, ...] = (
    # Price
    "close", "open", "high", "low", "volume", "close_vs_open_pct",
    # Trend
    "ema50", "ema200",
    "close_vs_ema50_pct", "close_vs_ema200_pct", "ema50_vs_ema200_pct",
    # Momentum
    "rsi14", "macd_line", "macd_signal", "macd_histogram", "roc10",
    # Volatility
    "atr14", "atr14_pct_of_price", "bb_upper", "bb_lower", "bb_pct_b",
    # Volume
    "volume_vs_20avg", "obv_slope_5",
    # Trend strength
    "adx14", "plus_di", "minus_di",
    # Setup-specific
    "setup_name", "raw_score", "rr_ratio", "direction",
    # Context
    "portfolio", "timeframe",
)


def _clean(value: Any) -> Any:
    """Convert numpy scalars and NaN/inf to JSON-safe Python types."""
    if value is None:
        return None
    # Unwrap numpy scalars
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    # str, int, bool pass through
    return value


def _pct_diff(a: float | None, b: float | None) -> float | None:
    """(a - b) / b * 100, returns None on any NaN or zero denominator."""
    if a is None or b is None:
        return None
    if b == 0:
        return None
    result = (a - b) / b * 100
    return _clean(result)


class FeatureExtractor:
    """
    Extracts a fixed-schema feature vector for a single setup fire.

    Usage::

        extractor = FeatureExtractor()
        features = extractor.extract("AAPL", df, setup_result)
    """

    # Keep a public reference so tests can compare against it
    EXPECTED_KEYS: tuple[str, ...] = _EXPECTED_KEYS

    def extract(
        self,
        symbol: str,  # noqa: ARG002 — reserved for future DB queries
        df: pd.DataFrame,
        setup_result: SetupResult,
    ) -> dict[str, Any]:
        """
        Compute all features from the last bar of *df* and merge
        setup-level fields from *setup_result*.

        Returns a flat dict with JSON-serialisable values.
        """
        # ── Indicator series ──────────────────────────────────────────
        close_s = df["close"]
        open_s = df["open"]
        high_s = df["high"]
        low_s = df["low"]
        vol_s = df["volume"]

        ema50_s = calc_ema(close_s, 50)
        ema200_s = calc_ema(close_s, 200)
        rsi_s = calc_rsi(close_s, 14)
        macd_line_s, macd_sig_s, macd_hist_s = calc_macd(close_s)
        roc_s = calc_roc(close_s, 10)
        atr_s = calc_atr(high_s, low_s, close_s, 14)
        bb_upper_s, _bb_mid, bb_lower_s = bollinger_bands(close_s, 20, 2.0)
        bb_pct_b_s = bollinger_pct_b(close_s, 20, 2.0)
        obv_s = calc_obv(close_s, vol_s)
        adx_s, plus_di_s, minus_di_s = calc_adx(high_s, low_s, close_s, 14)
        vol_avg20_s = vol_s.rolling(20, min_periods=1).mean()

        # ── Last-bar scalars ──────────────────────────────────────────
        def last(s: pd.Series) -> float | None:
            val = s.iloc[-1]
            return _clean(val)

        close = last(close_s)
        open_ = last(open_s)
        high = last(high_s)
        low = last(low_s)
        volume = _clean(float(vol_s.iloc[-1]))

        ema50 = last(ema50_s)
        ema200 = last(ema200_s)
        atr14 = last(atr_s)

        # OBV slope over last 5 bars
        if len(obv_s) >= 5:
            obv_tail = obv_s.iloc[-5:].values
            x = np.arange(5, dtype=float)
            # Simple linear regression slope
            slope = float(np.polyfit(x, obv_tail, 1)[0])
            obv_slope_5 = _clean(slope)
        else:
            obv_slope_5 = None

        # volume_vs_20avg ratio
        vol_avg20 = last(vol_avg20_s)
        if vol_avg20 and vol_avg20 > 0 and volume is not None:
            volume_vs_20avg = _clean(volume / vol_avg20)
        else:
            volume_vs_20avg = None

        # ATR as % of price
        if atr14 is not None and close is not None and close > 0:
            atr14_pct_of_price = _clean(atr14 / close * 100)
        else:
            atr14_pct_of_price = None

        return {
            # ── Price ─────────────────────────────────────────────────
            "close": close,
            "open": open_,
            "high": high,
            "low": low,
            "volume": volume,
            "close_vs_open_pct": _pct_diff(close, open_),
            # ── Trend ─────────────────────────────────────────────────
            "ema50": ema50,
            "ema200": ema200,
            "close_vs_ema50_pct": _pct_diff(close, ema50),
            "close_vs_ema200_pct": _pct_diff(close, ema200),
            "ema50_vs_ema200_pct": _pct_diff(ema50, ema200),
            # ── Momentum ──────────────────────────────────────────────
            "rsi14": last(rsi_s),
            "macd_line": last(macd_line_s),
            "macd_signal": last(macd_sig_s),
            "macd_histogram": last(macd_hist_s),
            "roc10": last(roc_s),
            # ── Volatility ────────────────────────────────────────────
            "atr14": atr14,
            "atr14_pct_of_price": atr14_pct_of_price,
            "bb_upper": last(bb_upper_s),
            "bb_lower": last(bb_lower_s),
            "bb_pct_b": last(bb_pct_b_s),
            # ── Volume ────────────────────────────────────────────────
            "volume_vs_20avg": volume_vs_20avg,
            "obv_slope_5": obv_slope_5,
            # ── Trend strength ────────────────────────────────────────
            "adx14": last(adx_s),
            "plus_di": last(plus_di_s),
            "minus_di": last(minus_di_s),
            # ── Setup-specific ────────────────────────────────────────
            "setup_name": setup_result.setup_name,
            "raw_score": _clean(setup_result.score),
            "rr_ratio": _clean(setup_result.rr_ratio),
            "direction": str(setup_result.direction),
            # ── Context ───────────────────────────────────────────────
            "portfolio": str(setup_result.metadata.get("portfolio", "intra")),
            "timeframe": setup_result.timeframe,
        }
