"""Optional VectorBT execution for Strategy Backtest rows (research only).

A row's trades were decided and filled by the strategy's own bar loop. When the
caller asks for VectorBT execution, those closed trades are turned into orders
(per-family adapters), executed by ``vectorbt_replay.check_parity`` and compared
with the bar loop. Only when the replay matches with zero mismatches does the
row take its per-trade net PnL, fees and net R from VectorBT's fills, and its
summary is recomputed by the same summarizer that built the row. Which trades
exist never changes. Any failure (no adapter, vectorbt not installed, parity
mismatch, unexpected error) leaves the bar-loop row untouched apart from the
annotations below.

Annotations added to the row (only when this module is called):

* ``execution_engine``: ``"vectorbt"`` on PASS, else ``"bar_loop"``
* ``vectorbt_parity``: ``ParityResult.to_dict()``, or
  ``{"verdict": "UNSUPPORTED" | "UNAVAILABLE" | "ERROR", "reason": ...}``
* ``vectorbt_mismatches``: int
* ``vectorbt_unrecomputed_fields``: list (COMBO rows on PASS only)

No paper, live, order or Telegram path imports this module.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import math
from typing import Any, Callable, Literal, Mapping, Sequence

Family = Literal["engine", "combo"]

_ADAPTERS: dict[str, tuple[str, str]] = {
    "engine": ("app.research.vectorbt_orders_engine", "engine_replay_orders"),
    "combo": ("app.research.vectorbt_orders_combo", "combo_replay_orders"),
}

# Engine rows: every key build_engine_row derives from the trades' net fields.
ENGINE_RECOMPUTED_FIELDS: tuple[str, ...] = (
    "sample_size",
    "average_R_net",
    "average_R_usd_basis_gross",
    "average_R_usd_basis_net",
    "profit_factor_net",
    "win_rate_net",
    "max_drawdown_usd_net",
    "equity_curve_usd_net",
    "r_definition",
    "expectancy_R",
    "profit_factor",
    "max_drawdown_R",
    "r_values",
    "equity_curve_r",
    "pnl_usd",
    "pnl_usd_net",
    "fees_usd",
)

# COMBO rows: build_matrix_row_postprocess (and the analytics-failure fallback in
# service.strategy_matrix) take these from summarize_net_trades + trade sums.
COMBO_NET_SUMMARY_FIELDS: tuple[str, ...] = (
    "average_R_net",
    "average_R_usd_basis_gross",
    "average_R_usd_basis_net",
    "profit_factor_net",
    "win_rate_net",
    "max_drawdown_usd_net",
    "equity_curve_usd_net",
    "r_definition",
)
# COMBO R-curve fields come from the engine's own price-based R metrics
# (metrics.compute_metrics / causal_r_metrics), not from USD fills, so VectorBT
# fills cannot recompute them. They stay bar-loop and are listed on the row.
COMBO_UNRECOMPUTED_FIELDS: tuple[str, ...] = ("r_values", "equity_curve_r", "max_drawdown_R")
# Causal (COMBO_02_V2) rows also carry these from the causal book summary.
COMBO_CAUSAL_UNRECOMPUTED_FIELDS: tuple[str, ...] = ("max_drawdown_usd", "win_rate")

_NET_ALIASES = ("net_pnl_usd", "net_pnl")
_FEE_ALIASES = ("fee_total_usd", "total_fee", "fees")


class _Unsupported(Exception):
    """The family adapter module or function is not there (yet)."""


def _is_replay_unsupported(exc: BaseException) -> bool:
    return any(c.__name__ == "ReplayUnsupported" for c in type(exc).__mro__)


def _adapter(family: str) -> Callable[..., list[Any]]:
    """Lazy import of the per-family order adapter. Missing -> unsupported."""
    mod_name, fn_name = _ADAPTERS[family]
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise _Unsupported(f"{mod_name} not available: {exc}") from exc
    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise _Unsupported(f"{mod_name}.{fn_name} not defined")
    return fn


def _vectorbt_available() -> str | None:
    """None when ``import vectorbt`` works, else the reason it does not."""
    try:
        import vectorbt  # noqa: F401
    except ImportError as exc:
        return f"vectorbt is not installed in this environment ({exc})"
    except Exception as exc:  # noqa: BLE001 - broken install, numba issues, ...
        return f"vectorbt failed to import: {type(exc).__name__}: {exc}"
    return None


def _call_adapter(fn: Callable[..., list[Any]], strategy_id: str, trades: list[dict[str, Any]], extra: Mapping[str, Any]) -> list[Any]:
    kwargs: dict[str, Any] = {}
    if extra:
        try:
            params = inspect.signature(fn).parameters
            accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
            kwargs = {k: v for k, v in extra.items() if accepts_any or k in params}
        except (TypeError, ValueError):
            kwargs = {}
    return list(fn(strategy_id, trades, **kwargs))


def _annotate(row: Mapping[str, Any], engine: str, parity: dict[str, Any], mismatches: int) -> dict[str, Any]:
    out = dict(row)
    out["execution_engine"] = engine
    out["vectorbt_parity"] = parity
    out["vectorbt_mismatches"] = int(mismatches)
    return out


def _map_replayed(trades: Sequence[Mapping[str, Any]], orders: Sequence[Any], replayed: Sequence[Any]) -> list[Any]:
    """Replayed trade for each row trade, in row order.

    Adapters emit one order per row trade in row order; the replayed trades
    carry the order's ``trade_key``. Raises ValueError when that does not hold.
    """
    if len(orders) != len(trades):
        raise ValueError(f"adapter produced {len(orders)} orders for {len(trades)} row trades")
    keys = [str(o.trade_key) for o in orders]
    if len(set(keys)) != len(keys):
        raise ValueError("adapter trade keys are not unique")
    by_key: dict[str, Any] = {}
    for v in replayed:
        k = None if v.trade_key is None else str(v.trade_key)
        if k is None or k in by_key:
            raise ValueError(f"replayed trade key {k!r} missing or repeated")
        by_key[k] = v
    out = []
    for k in keys:
        if k not in by_key:
            raise ValueError(f"no replayed trade for {k!r}")
        out.append(by_key[k])
    return out


def _fee_sign(trade: Mapping[str, Any]) -> float:
    cur = trade.get("fee_total_usd")
    if isinstance(cur, (int, float)) and cur != 0:
        return -1.0 if cur < 0 else 1.0
    return 1.0 if str(trade.get("fee_sign") or "").upper() == "POSITIVE_COST" else -1.0


def _substitute(trade: Mapping[str, Any], rep: Any) -> dict[str, Any]:
    """Copy of ``trade`` with net PnL, total fees and net R from VectorBT."""
    t = copy.deepcopy(dict(trade))
    old_net = t.get("net_pnl_usd")
    old_fee = t.get("fee_total_usd")
    new_fee = _fee_sign(t) * float(rep.fees_usd)
    # Aliases (net_pnl, total_fee, fees) follow only when they held the same value.
    for name in _NET_ALIASES[1:]:
        if _same(t.get(name), old_net):
            t[name] = rep.pnl_usd
    for name in _FEE_ALIASES[1:]:
        if _same(t.get(name), old_fee):
            t[name] = new_fee
    t["net_pnl_usd"] = rep.pnl_usd
    t["fee_total_usd"] = new_fee
    t["r_net"] = rep.net_r
    return t


def _same(a: Any, b: Any) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)


def _recompute_engine(row: Mapping[str, Any], trades: list[dict[str, Any]], strategy_id: str) -> dict[str, Any]:
    from app.research.engine_backtest import build_engine_row

    rebuilt = build_engine_row(
        {
            "trades": trades,
            "open_trades_at_end": row.get("open_trades_at_end"),
            "status": row.get("status"),
            "execution_model": row.get("execution_model"),
        },
        strategy_id=strategy_id,
        direction=str(row.get("direction") or ""),
        symbol=str(row.get("symbol") or ""),
        timeframe=str(row.get("timeframe") or ""),
        risk_usd=float(row.get("risk_usd") or 0.0),
        principal=float(row.get("principal_usd") or 0.0),
        leverage=float(row.get("leverage") or 0.0),
        bars_loaded=int(row.get("bars_loaded") or 0),
        warmup_bars=int(row.get("warmup_bars") or 0),
        candle_source=row.get("candle_source"),
        period_start=row.get("period_start"),
        period_end=row.get("period_end"),
        include_trades=True,
    )
    out = dict(row)
    for key in ENGINE_RECOMPUTED_FIELDS:
        if key in row:
            out[key] = rebuilt[key]
    out["trades"] = trades
    return out


def _recompute_combo(row: Mapping[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    from app.research.trade_fees import summarize_net_trades

    net = summarize_net_trades(trades, risk_usd=float(row.get("risk_usd") or 0.0))
    out = dict(row)
    for key in COMBO_NET_SUMMARY_FIELDS:
        if key in row:
            out[key] = net[key]
    nets = [float(t["net_pnl_usd"]) for t in trades if t.get("net_pnl_usd") is not None]
    if "pnl_usd_net" in row:
        out["pnl_usd_net"] = sum(nets) if nets else None
    if "fees_usd" in row:
        out["fees_usd"] = sum(float(t.get("fee_total_usd") or 0) for t in trades)
    out["trades"] = trades
    unrecomputed = [k for k in COMBO_UNRECOMPUTED_FIELDS if k in row]
    if row.get("pnl_model") is not None or "causal_trades" in row:
        unrecomputed += [k for k in COMBO_CAUSAL_UNRECOMPUTED_FIELDS if k in row]
    out["vectorbt_unrecomputed_fields"] = unrecomputed
    return out


def apply_vectorbt_execution(
    row: dict[str, Any],
    *,
    strategy_id: str,
    family: Family,
    adapter_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ``row`` (shallow copy) with VectorBT execution applied when parity passes.

    The input row is never mutated. ``adapter_kwargs`` are forwarded only when
    the adapter's signature accepts them (e.g. ``taker_fee`` for engine rows).
    """
    if family not in _ADAPTERS:
        raise ValueError(f"family must be 'engine' or 'combo', got {family!r}")
    trades = [t for t in (row.get("trades") or []) if isinstance(t, Mapping)]
    if not trades:
        return _annotate(
            row, "bar_loop", {"verdict": "UNAVAILABLE", "reason": "row has no trades to replay"}, 0
        )
    reason = _vectorbt_available()
    if reason is not None:
        return _annotate(row, "bar_loop", {"verdict": "UNAVAILABLE", "reason": reason}, 0)
    try:
        fn = _adapter(family)
        orders = _call_adapter(fn, strategy_id, [dict(t) for t in trades], adapter_kwargs or {})
    except _Unsupported as exc:
        return _annotate(row, "bar_loop", {"verdict": "UNSUPPORTED", "reason": str(exc)}, 0)
    except Exception as exc:  # noqa: BLE001
        if _is_replay_unsupported(exc):
            return _annotate(row, "bar_loop", {"verdict": "UNSUPPORTED", "reason": str(exc)}, 0)
        return _annotate(
            row,
            "bar_loop",
            {"verdict": "ERROR", "reason": f"adapter error: {type(exc).__name__}: {exc}"},
            0,
        )

    from app.research.vectorbt_replay import check_parity

    result = check_parity(orders)
    parity = result.to_dict()
    if not result.passed:
        return _annotate(row, "bar_loop", parity, result.mismatches)
    try:
        replayed = _map_replayed(trades, orders, result.replayed)
        new_trades = [_substitute(t, rep) for t, rep in zip(trades, replayed)]
        if family == "engine":
            out = _recompute_engine(row, new_trades, strategy_id)
        else:
            out = _recompute_combo(row, new_trades)
    except Exception as exc:  # noqa: BLE001
        parity = {**parity, "verdict": "UNMAPPED", "reason": f"{type(exc).__name__}: {exc}"}
        return _annotate(row, "bar_loop", parity, result.mismatches)
    out["execution_engine"] = "vectorbt"
    out["vectorbt_parity"] = parity
    out["vectorbt_mismatches"] = 0
    return out


__all__ = ["apply_vectorbt_execution"]
