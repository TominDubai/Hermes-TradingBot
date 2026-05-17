from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hermes.data.base import Timeframe

UNIVERSES_DIR = Path(__file__).parent.parent.parent.parent / "data" / "universes"


@dataclass
class Universe:
    name: str
    description: str
    provider: str
    timeframe: Timeframe
    scan_frequency: str
    symbols: list[str]
    min_dollar_volume: int | None = None
    extra: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.symbols)


def load_universe(name: str, universes_dir: Path = UNIVERSES_DIR) -> Universe:
    """Load a universe by name from the universes directory."""
    path = universes_dir / f"{name}.yaml"
    if not path.exists():
        available = [p.stem for p in universes_dir.glob("*.yaml")]
        raise FileNotFoundError(
            f"Universe '{name}' not found. Available: {sorted(available)}"
        )

    with path.open() as f:
        data = yaml.safe_load(f)

    known_fields = {"name", "description", "provider", "timeframe",
                    "scan_frequency", "symbols", "min_dollar_volume"}
    extra = {k: v for k, v in data.items() if k not in known_fields}

    return Universe(
        name=data["name"],
        description=data.get("description", ""),
        provider=data["provider"],
        timeframe=Timeframe(data["timeframe"]),
        scan_frequency=data["scan_frequency"],
        symbols=data["symbols"],
        min_dollar_volume=data.get("min_dollar_volume"),
        extra=extra,
    )


def list_universes(universes_dir: Path = UNIVERSES_DIR) -> list[str]:
    """Return names of all available universes."""
    return sorted(p.stem for p in universes_dir.glob("*.yaml"))
