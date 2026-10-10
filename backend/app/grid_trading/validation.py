"""In-sample / out-of-sample split, walk-forward, and a small grid-count sweep.

Each window is a fresh run on its own bars. Later windows are not visible to
earlier ones.
"""

from __future__ import annotations

from typing import Any

from app.grid_trading.config import GridConfig, GridConfigError
from app.grid_trading.engine import run_grid
from app.grid_trading.metrics import summarize


def split_sample(candles: list[dict[str, Any]], config: GridConfig, *, is_fraction: float = 0.7) -> dict[str, Any]:
    if not 0.1 <= is_fraction <= 0.9:
        raise GridConfigError("is_fraction", "is_fraction must be between 0.1 and 0.9")
    cut = int(len(candles) * is_fraction)
    if cut < 2 or len(candles) - cut < 2:
        raise GridConfigError("candles", "not enough candles to split in-sample and out-of-sample")
    ins = run_grid(candles[:cut], config)
    oos = run_grid(candles[cut:], config)
    return {
        "is_fraction": is_fraction,
        "in_sample_bars": cut,
        "out_of_sample_bars": len(candles) - cut,
        "in_sample": summarize(ins),
        "out_of_sample": summarize(oos),
    }


def walk_forward(
    candles: list[dict[str, Any]],
    config: GridConfig,
    *,
    train_bars: int,
    test_bars: int,
) -> dict[str, Any]:
    if train_bars < 2 or test_bars < 2:
        raise GridConfigError("walk_forward", "train_bars and test_bars must be >= 2")
    windows = []
    start = 0
    while start + train_bars + test_bars <= len(candles):
        train = candles[start : start + train_bars]
        test = candles[start + train_bars : start + train_bars + test_bars]
        windows.append(
            {
                "start": start,
                "train": summarize(run_grid(train, config)),
                "test": summarize(run_grid(test, config)),
            }
        )
        start += test_bars
    if not windows:
        raise GridConfigError("walk_forward", "candles do not cover one train plus test window")
    return {"train_bars": train_bars, "test_bars": test_bars, "windows": windows}


def sweep_grid_count(
    candles: list[dict[str, Any]],
    config: GridConfig,
    grid_counts: list[int],
) -> dict[str, Any]:
    if not grid_counts:
        raise GridConfigError("sweep", "grid_counts is empty")
    rows = []
    for count in grid_counts:
        trial = config.model_copy(update={"grid_count": count})
        try:
            result = run_grid(candles, trial)
        except GridConfigError as exc:
            rows.append({"grid_count": count, "accepted": False, "error": exc.code, "message": str(exc)})
            continue
        summary = summarize(result)
        summary["grid_count"] = count
        rows.append(summary)
    return {"rows": rows}
