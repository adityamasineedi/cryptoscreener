"""In-sample / out-of-sample split, walk-forward, and a small grid-count sweep.

Each window is a fresh run on its own bars. Later windows are not visible to
earlier ones.
"""

from __future__ import annotations

from typing import Any

from app.grid_trading.config import GRID_COUNT_MAX, GRID_COUNT_MIN, GridConfig, GridConfigError
from app.grid_trading.engine import run_grid
from app.grid_trading.funding import stamp
from app.grid_trading.metrics import summarize

OVERFIT_WARNING = (
    "Parameter sweeps overfit. Rank is the share of nearby sets with positive net PnL: "
    "grid count within ±20% and range width at ±10%. It is not the single best PnL."
)


def _funding_for(candles: list[dict[str, Any]], funding: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Hide settlements after this slice. Earlier rows stay as cadence context."""
    if not funding or not candles:
        return funding
    start = stamp(candles[0]["time"])
    end = stamp(candles[-1]["time"])
    visible = []
    for row in funding:
        when = stamp(row.get("time"))
        if when > end:
            continue
        item = dict(row)
        if when < start:
            item["context"] = True
        visible.append(item)
    return visible


def split_sample(
    candles: list[dict[str, Any]],
    config: GridConfig,
    *,
    is_fraction: float = 0.7,
    funding: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not 0.1 <= is_fraction <= 0.9:
        raise GridConfigError("is_fraction", "is_fraction must be between 0.1 and 0.9")
    cut = int(len(candles) * is_fraction)
    if cut < 2 or len(candles) - cut < 2:
        raise GridConfigError("candles", "not enough candles to split in-sample and out-of-sample")
    ins = run_grid(candles[:cut], config, _funding_for(candles[:cut], funding))
    oos = run_grid(candles[cut:], config, _funding_for(candles[cut:], funding))
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
    funding: list[dict[str, Any]] | None = None,
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
                "train": summarize(run_grid(train, config, _funding_for(train, funding))),
                "test": summarize(run_grid(test, config, _funding_for(test, funding))),
            }
        )
        start += test_bars
    if not windows:
        raise GridConfigError("walk_forward", "candles do not cover one train plus test window")
    return {"train_bars": train_bars, "test_bars": test_bars, "windows": windows}


def _neighbor_configs(config: GridConfig) -> list[GridConfig]:
    low_count = max(GRID_COUNT_MIN, int(round(config.grid_count * 0.8)))
    high_count = min(GRID_COUNT_MAX, int(round(config.grid_count * 1.2)))
    counts = {count for count in range(low_count, high_count + 1) if count != config.grid_count}
    if not counts:
        if config.grid_count > GRID_COUNT_MIN:
            counts.add(config.grid_count - 1)
        if config.grid_count < GRID_COUNT_MAX:
            counts.add(config.grid_count + 1)
    neighbors: list[GridConfig] = []
    for count in sorted(counts):
        neighbors.append(
            config.model_copy(update={"grid_count": count, "max_open_orders": max(config.max_open_orders, count)})
        )
    mid = (config.range_low + config.range_high) / 2.0
    width = config.range_high - config.range_low
    for scale in (0.9, 1.1):
        half = width * scale / 2.0
        low = mid - half
        high = mid + half
        if low <= 0 or high <= low:
            continue
        stop = config.stop_price if config.stop_price < low else low * 0.99
        neighbors.append(config.model_copy(update={"range_low": low, "range_high": high, "stop_price": stop}))
    return neighbors


def robustness(
    candles: list[dict[str, Any]],
    config: GridConfig,
    funding: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    visible = _funding_for(candles, funding)
    if config.funding_mode == "charge":
        probe = run_grid(candles, config, visible)
        if probe.get("funding_status") == "unavailable":
            return {
                "funding_status": "unavailable",
                "ranked": False,
                "robustness": None,
                "neighbors": 0,
                "profitable_neighbors": 0,
                "reason": (probe.get("warnings") or [""])[0],
            }
    neighbors = _neighbor_configs(config)
    profitable = 0
    for trial in neighbors:
        try:
            result = run_grid(candles, trial, visible)
        except GridConfigError:
            continue
        net = float(result.get("net_pnl") or 0.0)
        if result.get("accepted") and net > 0:
            profitable += 1
    total = len(neighbors)
    return {
        "neighbors": total,
        "profitable_neighbors": profitable,
        "robustness": (profitable / total) if total else 0.0,
    }


def sweep_grid_count(
    candles: list[dict[str, Any]],
    config: GridConfig,
    grid_counts: list[int],
    funding: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not grid_counts:
        raise GridConfigError("sweep", "grid_counts is empty")
    if config.funding_mode == "charge":
        probe = run_grid(candles, config, _funding_for(candles, funding))
        if probe.get("funding_status") == "unavailable":
            return {
                "funding_status": "unavailable",
                "ranked": False,
                "rows": [],
                "reason": (probe.get("warnings") or [""])[0],
                "pnl_before_funding": probe.get("pnl_before_funding"),
                "warning": (probe.get("warnings") or [""])[0],
            }
    rows = []
    for count in grid_counts:
        trial = config.model_copy(update={"grid_count": count, "max_open_orders": max(config.max_open_orders, count)})
        try:
            result = run_grid(candles, trial, _funding_for(candles, funding))
        except GridConfigError as exc:
            rows.append(
                {
                    "grid_count": count,
                    "accepted": False,
                    "error": exc.code,
                    "message": str(exc),
                    "robustness": 0.0,
                    "net_pnl": None,
                }
            )
            continue
        summary = summarize(result)
        summary["grid_count"] = count
        summary.update(robustness(candles, trial, funding))
        rows.append(summary)
    rows.sort(
        key=lambda row: (
            -float(row.get("robustness") or 0.0),
            -float(row.get("net_pnl") or 0.0),
            int(row.get("grid_count") or 0),
        )
    )
    report: dict[str, Any] = {"warning": OVERFIT_WARNING, "rows": rows, "ranked": True}
    if config.funding_mode == "exclude":
        report["funding_status"] = "excluded"
        report["label"] = "funding excluded"
    else:
        report["funding_status"] = "charged"
    return report
