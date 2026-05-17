"""
Pure-pandas indicator library. No ta-lib dependency — everything computed
from OHLCV DataFrames. All functions return pd.Series aligned to the input index.

Convention:
  - Input df must have columns: open, high, low, close, volume (float64)
  - DatetimeIndex, UTC-aware, ascending
  - All outputs are pd.Series with the same index
  - NaN is acceptable at the head (warm-up period)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Trend ─────────────────────────────────────────────────────────────────────

def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


def dema(close: pd.Series, period: int) -> pd.Series:
    """Double EMA — reduces lag vs single EMA."""
    e = ema(close, period)
    return 2 * e - ema(e, period)


# ── Momentum ──────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def roc(close: pd.Series, period: int = 10) -> pd.Series:
    """Rate of Change (%)."""
    return close.pct_change(periods=period) * 100


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator (%K, %D)."""
    lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
    highest_high = high.rolling(window=k_period, min_periods=k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    k = 100 * (close - lowest_low) / denom
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return k, d


# ── Volatility ────────────────────────────────────────────────────────────────

def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands: upper, middle, lower."""
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def bollinger_pct_b(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
    """Bollinger %B: position within bands (0=lower, 1=upper)."""
    upper, _, lower = bollinger_bands(close, period, std_dev)
    denom = (upper - lower).replace(0, np.nan)
    return (close - lower) / denom


def keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channel: upper, middle, lower."""
    middle = ema(close, ema_period)
    a = atr(high, low, close, atr_period)
    upper = middle + multiplier * a
    lower = middle - multiplier * a
    return upper, middle, lower


def donchian_channel(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channel: upper, middle, lower."""
    upper = high.rolling(window=period, min_periods=period).max()
    lower = low.rolling(window=period, min_periods=period).min()
    middle = (upper + lower) / 2
    return upper, middle, lower


# ── Volume ────────────────────────────────────────────────────────────────────

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Money Flow Index."""
    typical_price = (high + low + close) / 3
    raw_mf = typical_price * volume
    positive_mf = raw_mf.where(typical_price > typical_price.shift(1), 0)
    negative_mf = raw_mf.where(typical_price < typical_price.shift(1), 0)
    pos_sum = positive_mf.rolling(window=period, min_periods=period).sum()
    neg_sum = negative_mf.rolling(window=period, min_periods=period).sum()
    mfr = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + mfr))


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """
    VWAP — session-level (resets each day).
    Works on intraday data with DatetimeIndex.
    For daily data, VWAP = typical price (no intraday session boundary).
    """
    typical_price = (high + low + close) / 3
    tpv = typical_price * volume

    # Group by date to reset each session
    date_groups = tpv.index.normalize()  # type: ignore[union-attr]
    cumulative_tpv = tpv.groupby(date_groups).cumsum()
    cumulative_vol = volume.groupby(date_groups).cumsum()
    return cumulative_tpv / cumulative_vol.replace(0, np.nan)


# ── Trend strength ────────────────────────────────────────────────────────────

def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX, +DI, -DI."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = high - prev_high
    minus_dm = prev_low - low

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    tr_val = atr(high, low, close, period)  # reuse ATR (already smoothed)
    # Smooth DM with Wilder
    smooth_plus = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    smooth_minus = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    atr_val = tr_val  # already smoothed ATR
    plus_di = 100 * smooth_plus / atr_val.replace(0, np.nan)
    minus_di = 100 * smooth_minus / atr_val.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return adx_val, plus_di, minus_di


# ── Support / Resistance helpers ──────────────────────────────────────────────

def pivot_highs(high: pd.Series, left: int = 5, right: int = 5) -> pd.Series:
    """Returns a boolean Series marking bars that are local pivot highs."""
    result = pd.Series(False, index=high.index)
    for i in range(left, len(high) - right):
        window = high.iloc[i - left: i + right + 1]
        if high.iloc[i] == window.max():
            result.iloc[i] = True
    return result


def pivot_lows(low: pd.Series, left: int = 5, right: int = 5) -> pd.Series:
    """Returns a boolean Series marking bars that are local pivot lows."""
    result = pd.Series(False, index=low.index)
    for i in range(left, len(low) - right):
        window = low.iloc[i - left: i + right + 1]
        if low.iloc[i] == window.min():
            result.iloc[i] = True
    return result
