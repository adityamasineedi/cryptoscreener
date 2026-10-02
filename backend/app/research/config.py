"""Research-module configuration. Isolated from live SignalConfig defaults.

Changing these values does NOT alter live signal thresholds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

# Bump when research engine semantics change (reproducibility).
RESEARCH_ENGINE_VERSION = "bos_research_v2_tp1_min_rr"
SIGNAL_ENGINE_VERSION = "setup_signal_v1"

# Ambiguous same-candle SL/TP handling
AMBIGUOUS_CONSERVATIVE = "CONSERVATIVE"  # count as SL
AMBIGUOUS_NEUTRAL = "NEUTRAL"  # count as AMBIGUOUS_INTRABAR, R=0
AMBIGUOUS_EXCLUDE = "EXCLUDE"  # drop from metric denominators


@dataclass
class ResearchConfig:
    """Controlled research parameters — not auto-optimized."""

    min_rr: float = 2.0
    min_rvol: float = 1.5
    atr_multiplier: float = 1.5
    ambiguous_handling: str = AMBIGUOUS_CONSERVATIVE
    min_bars: int = 50
    min_coverage_ratio: float = 0.85
    max_gap_bars: int = 3
    # Out-of-sample splits (fractions of chronological history)
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    oos_fraction: float = 0.20
    # Walk-forward (bars, when enabled)
    walk_forward_train_bars: int = 90 * 96  # ~90d of 15m if used as bars
    walk_forward_test_bars: int = 30 * 96
    # Cap-group thresholds (reuse screener preset semantics; not coin lists)
    large_cap_min: float = 10_000_000_000.0
    mid_cap_min: float = 1_000_000_000.0
    mid_cap_max: float = 10_000_000_000.0
    small_cap_min: float = 50_000_000.0
    small_cap_max: float = 1_000_000_000.0
    # Parameter experiment grids (only used when explicitly requested)
    parameter_grid: dict[str, list[float]] = field(default_factory=dict)
    multiple_testing_flag_threshold: int = 8

    def configuration_dict(self) -> dict[str, Any]:
        return asdict(self)

    def configuration_hash(self) -> str:
        raw = json.dumps(self.configuration_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> ResearchConfig:
        cfg = cls()
        if not raw:
            return cfg
        for key, value in raw.items():
            if hasattr(cfg, key) and key != "parameter_grid":
                setattr(cfg, key, value)
        grid = raw.get("parameter_grid")
        if isinstance(grid, dict):
            cfg.parameter_grid = {
                str(k): [float(x) for x in v] for k, v in grid.items() if isinstance(v, (list, tuple))
            }
        return cfg

    def with_overrides(self, **kwargs: Any) -> ResearchConfig:
        data = self.configuration_dict()
        data.update(kwargs)
        return ResearchConfig.from_mapping(data)


def classify_asset_group(
    symbol: str,
    market_cap: float | None,
    *,
    config: ResearchConfig | None = None,
) -> str:
    """Classify by market-cap filters (preset semantics). Never by hardcoded coin lists.

    BTC/ETH labels are applied only when the symbol base is BTC or ETH AND
    market_cap is available; otherwise large/mid/small/unknown from mcap.
    """
    cfg = config or ResearchConfig()
    base = symbol.upper().replace("USDT", "").replace("BUSD", "").replace("USD", "")
    if market_cap is None or market_cap <= 0:
        if base == "BTC":
            return "BTC"
        if base == "ETH":
            return "ETH"
        return "UNKNOWN"
    if base == "BTC":
        return "BTC"
    if base == "ETH":
        return "ETH"
    if market_cap >= cfg.large_cap_min:
        return "large-cap"
    if cfg.mid_cap_min <= market_cap < cfg.mid_cap_max:
        return "mid-cap"
    if cfg.small_cap_min <= market_cap < cfg.small_cap_max:
        return "small-cap"
    return "UNKNOWN"


def split_period_indices(
    n: int,
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    oos_fraction: float = 0.20,
) -> dict[str, tuple[int, int]]:
    """Return half-open [start, end) index ranges for train/validation/OOS."""
    if n <= 0:
        return {
            "TRAINING_PERIOD": (0, 0),
            "VALIDATION_PERIOD": (0, 0),
            "OUT_OF_SAMPLE_PERIOD": (0, 0),
        }
    total = train_fraction + validation_fraction + oos_fraction
    tf, vf, of = train_fraction / total, validation_fraction / total, oos_fraction / total
    t_end = int(n * tf)
    v_end = t_end + int(n * vf)
    if v_end >= n:
        v_end = max(t_end, n - 1)
    return {
        "TRAINING_PERIOD": (0, t_end),
        "VALIDATION_PERIOD": (t_end, v_end),
        "OUT_OF_SAMPLE_PERIOD": (v_end, n),
    }


def walk_forward_windows(
    n: int,
    *,
    train_bars: int,
    test_bars: int,
) -> list[dict[str, int]]:
    """Rolling train/test windows. Indices are half-open [start, end)."""
    windows: list[dict[str, int]] = []
    if train_bars <= 0 or test_bars <= 0 or n < train_bars + test_bars:
        return windows
    start = 0
    while start + train_bars + test_bars <= n:
        train_start = start
        train_end = start + train_bars
        test_start = train_end
        test_end = train_end + test_bars
        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
            }
        )
        start += test_bars
    return windows


def count_parameters_tested(grid: Mapping[str, Sequence[float]] | None) -> int:
    if not grid:
        return 1
    total = 1
    for values in grid.values():
        total *= max(1, len(list(values)))
    return total
