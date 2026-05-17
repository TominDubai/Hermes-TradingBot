"""
Setup registry — all setup detectors.
"""
from hermes.setups.base import Setup, SetupResult
from hermes.setups.breakout_consolidation import BreakoutConsolidation
from hermes.setups.cup_and_handle import CupAndHandle
from hermes.setups.mean_reversion import MeanReversion

__all__ = [
    "Setup",
    "SetupResult",
    "CupAndHandle",
    "MeanReversion",
    "BreakoutConsolidation",
]
