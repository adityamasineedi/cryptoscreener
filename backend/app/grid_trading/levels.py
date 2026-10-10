"""Arithmetic and geometric grid prices. The top level is snapped to range_high."""

from __future__ import annotations

from app.grid_trading.config import GridConfig, GridConfigError


def build_levels(config: GridConfig) -> list[float]:
    low = config.range_low
    high = config.range_high
    n = config.grid_count
    if n < 1:
        raise GridConfigError("grid_count", "grid_count must be >= 1")
    if low <= 0 or high <= low:
        raise GridConfigError("range", "range_low must be > 0 and below range_high")
    if config.grid_type == "arithmetic":
        step = (high - low) / n
        levels = [low + i * step for i in range(n + 1)]
    else:
        ratio = (high / low) ** (1.0 / n)
        levels = [low * (ratio**i) for i in range(n + 1)]
    levels[0] = low
    levels[-1] = high
    if any(levels[i] >= levels[i + 1] for i in range(n)):
        raise GridConfigError("range", "levels are not strictly increasing")
    return levels
