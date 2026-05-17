"""
Setup base class and result model.

Every setup implements Setup.detect(df) -> SetupResult | None
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from hermes.events.types import Direction, Portfolio


@dataclass
class SetupResult:
    """
    The output of a successful setup detection.

    score:      Raw float score from this setup (0.0–1.0).
                The rule scorer multiplies by confluence factors to get
                the final confluence_score (1–6).
    entry:      Suggested entry price.
    stop:       Stop-loss price (hard invalidation).
    target:     Take-profit target.
    direction:  LONG or SHORT.
    setup_name: Matches the Setup.name attribute.
    timeframe:  Timeframe string this setup fired on (e.g. "1d", "15m").
    metadata:   Arbitrary dict logged to telemetry for ML training.
                Include all raw indicator values that influenced detection.
    """
    score: float
    entry: float
    stop: float
    target: float
    direction: Direction
    setup_name: str
    timeframe: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def risk(self) -> float:
        """Distance from entry to stop (always positive)."""
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        """Distance from entry to target (always positive)."""
        return abs(self.target - self.entry)

    @property
    def rr_ratio(self) -> float:
        """Reward-to-risk ratio."""
        if self.risk == 0:
            return 0.0
        return self.reward / self.risk


class Setup(ABC):
    """
    Base class for all setup detectors.

    Subclasses implement detect() to return a SetupResult when
    their pattern is present on the last bar of the DataFrame,
    or None if not present.
    """

    #: Unique kebab-case name. Must match the file name.
    name: str

    #: Which portfolio this setup belongs to.
    portfolio: Portfolio

    #: Minimum number of bars required before the setup can fire.
    min_bars: int = 200

    def validate(self, df: pd.DataFrame) -> bool:
        """Return True if df has enough data to run this setup."""
        return (
            len(df) >= self.min_bars
            and all(c in df.columns for c in ["open", "high", "low", "close", "volume"])
        )

    @abstractmethod
    def detect(self, df: pd.DataFrame) -> SetupResult | None:
        """
        Analyse df and return a SetupResult if the pattern is present
        on the most recent bar, else None.

        df is guaranteed to be a clean OHLCV DataFrame (UTC index,
        ascending, no NaNs in OHLCV columns) — validated by the scanner
        before calling detect().
        """
        ...
