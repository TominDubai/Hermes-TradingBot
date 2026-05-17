"""Debug script for BreakoutConsolidation test."""
import numpy as np
import pandas as pd

def _make_dates(n):
    return pd.date_range("2023-01-01", periods=n, freq="B", tz="UTC")

n = 100
dates = _make_dates(n)
rng = np.random.default_rng(42)

close = np.empty(n)
high = np.empty(n)
low = np.empty(n)

base_trend = np.linspace(100, 120, 30)
noise = rng.normal(0, 0.15, 30)
for i in range(30):
    close[i] = base_trend[i] + noise[i]
    high[i] = close[i] + abs(rng.normal(0.25, 0.08))
    low[i] = close[i] - abs(rng.normal(0.25, 0.08))

consol_mid = 120.0
range_pct = 0.04
half_range = consol_mid * range_pct / 2

for j, i in enumerate(range(30, 99)):
    close[i] = consol_mid + half_range * np.sin(j * 0.35)
    high[i] = close[i] + half_range * 0.25
    low[i] = close[i] - half_range * 0.25

prior_donchian_upper = float(np.max(high[79:99]))
breakout_level = prior_donchian_upper * 1.02
close[99] = breakout_level
high[99] = breakout_level * 1.003
low[99] = breakout_level * 0.997

volume = np.full(n, 900_000.0)
volume[99] = 900_000.0 * 1.6

df = pd.DataFrame(
    {"open": close * 0.999, "high": high, "low": low, "close": close, "volume": volume},
    index=dates,
)

from hermes.indicators.core import adx, atr, donchian_channel

close_s = df["close"]
high_s = df["high"]
low_s = df["low"]

# Check Donchian
dc_upper, _, dc_lower = donchian_channel(high_s, low_s, 20)
donchian_upper = float(dc_upper.iloc[-2])
breakout_close = float(close_s.iloc[-1])
print(f"Donchian upper (bar -2): {donchian_upper:.4f}")
print(f"Breakout close: {breakout_close:.4f}")
print(f"Breaks out: {breakout_close > donchian_upper}")

# Check consolidation range
print("\n-- Consolidation scan --")
found = False
for lookback in range(20, 41):
    window_high = high_s.iloc[-(lookback+1):-1]
    window_low = low_s.iloc[-(lookback+1):-1]
    window_close = close_s.iloc[-(lookback+1):-1]
    ph = float(window_high.max())
    pl = float(window_low.min())
    mid = (ph + pl) / 2
    rp = (ph - pl) / mid
    in_range = ((window_close >= pl) & (window_close <= ph)).sum()
    print(f"  lookback {lookback}: range_pct={rp:.4f} in_range={in_range}")
    if rp < 0.08 and in_range >= 15:
        found = True

print(f"Consolidation found: {found}")

# Check ADX
adx_series, _, _ = adx(df["high"], df["low"], df["close"], 14)
consol_adx_window = adx_series.iloc[-21:-1]
adx_consol_mean = float(consol_adx_window.mean())
adx_current = float(adx_series.iloc[-1])
adx_prev = float(adx_series.iloc[-2])
print(f"\nADX consol mean (bars -21:-1): {adx_consol_mean:.4f}")
print(f"ADX current: {adx_current:.4f}")
print(f"ADX prev: {adx_prev:.4f}")
print(f"ADX rising: {adx_current > adx_prev}")
print(f"ADX consol < 20: {adx_consol_mean < 20}")
print(f"ADX current >= 20: {adx_current >= 20}")

# Volume
vol_s = df["volume"]
bv = float(vol_s.iloc[-1])
avg_v = float(vol_s.iloc[-21:-1].mean())
vr = bv / avg_v
print(f"\nVolume ratio: {vr:.4f} (need >= 1.3)")

# ATR
atr_series = atr(df["high"], df["low"], df["close"], 14)
atr_curr = float(atr_series.iloc[-1])
atr_5ago = float(atr_series.iloc[-6])
print(f"\nATR current: {atr_curr:.4f}")
print(f"ATR 5 ago:   {atr_5ago:.4f}")
print(f"ATR expanding: {atr_curr > atr_5ago}")
