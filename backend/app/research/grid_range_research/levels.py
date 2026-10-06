"""Equal-spaced grid level construction (research-only)."""

from __future__ import annotations


def grid_spacing(range_low: float, range_high: float, n_levels: int) -> float:
    """Spacing between consecutive levels for n_levels inclusive endpoints."""
    if n_levels < 2:
        raise ValueError("n_levels_must_be_at_least_2")
    if range_high <= range_low:
        raise ValueError("range_high_must_exceed_range_low")
    return (float(range_high) - float(range_low)) / float(n_levels - 1)


def build_grid_levels(
    range_low: float,
    range_high: float,
    n_levels: int,
) -> list[float]:
    """Build n equally spaced levels from range_low to range_high inclusive.

    Example: low=100, high=108, n=9 → 100,101,...,108
    """
    spacing = grid_spacing(range_low, range_high, n_levels)
    low = float(range_low)
    levels = [low + i * spacing for i in range(n_levels)]
    # Numerical hygiene: pin endpoints
    levels[0] = low
    levels[-1] = float(range_high)
    return levels


def level_index_touched(
    levels: list[float],
    *,
    bar_low: float,
    bar_high: float,
    tol: float = 0.0,
) -> list[int]:
    """Return indices of levels whose price was touched by [bar_low, bar_high]."""
    out: list[int] = []
    for i, px in enumerate(levels):
        if bar_low - tol <= px <= bar_high + tol:
            out.append(i)
    return out
