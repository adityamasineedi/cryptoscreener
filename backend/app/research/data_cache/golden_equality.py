"""Golden equality digests for research performance work.

Compares baseline vs optimized research outputs without changing strategy rules.
Floating-point fields use documented absolute tolerances.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

PRICE_TOL = 1e-10
R_TOL = 1e-12


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close(a: Any, b: Any, tol: float) -> bool:
    fa, fb = _f(a), _f(b)
    if fa is None and fb is None:
        return True
    if fa is None or fb is None:
        return False
    # profit_factor can be +inf when there are wins and zero losses
    if fa == fb:
        return True
    import math

    if math.isinf(fa) and math.isinf(fb) and (fa > 0) == (fb > 0):
        return True
    if math.isnan(fa) and math.isnan(fb):
        return True
    return abs(fa - fb) <= tol


def trade_digest(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Stable fields used for equality (timestamps / prices / R / exit)."""
    return {
        "symbol": trade.get("symbol"),
        "timeframe": trade.get("timeframe"),
        "direction": trade.get("direction"),
        "entry_index": trade.get("entry_index"),
        "exit_index": trade.get("exit_index"),
        "signal_time": trade.get("signal_time") or trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "entry_price": trade.get("entry_price"),
        "stop_price": trade.get("stop_price") or trade.get("stop"),
        "tp1": trade.get("tp1"),
        "tp2": trade.get("tp2"),
        "tp3": trade.get("tp3"),
        "outcome": trade.get("outcome") or trade.get("exit_reason"),
        "r_multiple": trade.get("r_multiple") or trade.get("r"),
    }


def compare_trades(
    baseline: Sequence[Mapping[str, Any]],
    optimized: Sequence[Mapping[str, Any]],
    *,
    price_tol: float = PRICE_TOL,
    r_tol: float = R_TOL,
) -> dict[str, Any]:
    """Exact-count + field-wise trade equality. Returns FIRST_MISMATCH on fail."""
    b = [trade_digest(t) for t in baseline]
    o = [trade_digest(t) for t in optimized]
    if len(b) != len(o):
        return {
            "status": "FIRST_MISMATCH",
            "stage": "trade_count",
            "baseline": len(b),
            "optimized": len(o),
        }
    price_keys = ("entry_price", "stop_price", "tp1", "tp2", "tp3")
    for i, (bt, ot) in enumerate(zip(b, o)):
        for k in (
            "symbol",
            "timeframe",
            "direction",
            "entry_index",
            "exit_index",
            "signal_time",
            "exit_time",
            "outcome",
        ):
            if bt.get(k) != ot.get(k):
                return {
                    "status": "FIRST_MISMATCH",
                    "stage": f"trade.{k}",
                    "index": i,
                    "baseline": bt.get(k),
                    "optimized": ot.get(k),
                    "baseline_trade": bt,
                    "optimized_trade": ot,
                }
        for k in price_keys:
            if not _close(bt.get(k), ot.get(k), price_tol):
                return {
                    "status": "FIRST_MISMATCH",
                    "stage": f"trade.{k}",
                    "index": i,
                    "baseline": bt.get(k),
                    "optimized": ot.get(k),
                    "baseline_trade": bt,
                    "optimized_trade": ot,
                }
        if not _close(bt.get("r_multiple"), ot.get("r_multiple"), r_tol):
            return {
                "status": "FIRST_MISMATCH",
                "stage": "trade.r_multiple",
                "index": i,
                "baseline": bt.get("r_multiple"),
                "optimized": ot.get("r_multiple"),
                "baseline_trade": bt,
                "optimized_trade": ot,
            }
    return {"status": "PASS", "trade_count": len(b)}


def compare_ohlcv_rows(
    baseline: Sequence[Mapping[str, Any]],
    optimized: Sequence[Mapping[str, Any]],
    *,
    price_tol: float = PRICE_TOL,
) -> dict[str, Any]:
    if len(baseline) != len(optimized):
        return {
            "status": "FIRST_MISMATCH",
            "stage": "ohlcv_count",
            "baseline": len(baseline),
            "optimized": len(optimized),
        }
    for i, (a, b) in enumerate(zip(baseline, optimized)):
        ta, tb = a.get("time"), b.get("time")
        if str(ta) != str(tb):
            return {
                "status": "FIRST_MISMATCH",
                "stage": "ohlcv.time",
                "index": i,
                "baseline": str(ta),
                "optimized": str(tb),
            }
        for k in ("open", "high", "low", "close", "volume"):
            if not _close(a.get(k), b.get(k), price_tol):
                return {
                    "status": "FIRST_MISMATCH",
                    "stage": f"ohlcv.{k}",
                    "index": i,
                    "baseline": a.get(k),
                    "optimized": b.get(k),
                }
    return {"status": "PASS", "rows": len(baseline)}


def metrics_digest(result: Mapping[str, Any] | None) -> dict[str, Any]:
    r = result or {}
    keys = (
        "sample_size",
        "average_R",
        "expectancy_R",
        "profit_factor",
        "max_drawdown_R",
        "tp1_hit_rate",
        "sl_rate",
    )
    return {k: r.get(k) for k in keys}


def compare_metrics(
    baseline: Mapping[str, Any] | None,
    optimized: Mapping[str, Any] | None,
    *,
    r_tol: float = R_TOL,
) -> dict[str, Any]:
    b = metrics_digest(baseline)
    o = metrics_digest(optimized)
    if b.get("sample_size") != o.get("sample_size"):
        return {
            "status": "FIRST_MISMATCH",
            "stage": "metrics.sample_size",
            "baseline": b.get("sample_size"),
            "optimized": o.get("sample_size"),
        }
    for k in ("average_R", "expectancy_R", "profit_factor", "max_drawdown_R", "tp1_hit_rate", "sl_rate"):
        if not _close(b.get(k), o.get(k), r_tol if "rate" not in k else 1e-12):
            # rates also tight
            if not _close(b.get(k), o.get(k), 1e-12):
                return {
                    "status": "FIRST_MISMATCH",
                    "stage": f"metrics.{k}",
                    "baseline": b.get(k),
                    "optimized": o.get(k),
                }
    return {"status": "PASS", "metrics": b}
