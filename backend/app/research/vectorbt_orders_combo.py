"""COMBO-family trades -> VectorBT replay orders (parity check only).

Input is exactly the ``trades`` list of one ``ResearchService.strategy_matrix``
row (what the Backtest screen row carries). Those dicts are
``combination_backtest.ResearchTrade.to_dict()`` rows enriched by
``trade_fees.enrich_trades`` inside ``backtest_cpu_worker.build_matrix_row_postprocess``.
For combination ``COMBO_02_V2`` (``execution_model == DUAL_LEGACY_AND_CAUSAL``)
the row's trades are instead the ``causal_execution`` CAUSAL_NEXT_OPEN book,
dollarized by the same ``enrich_trade_execution`` with ``slippage_rate=0``.

The adapter only reads what the engine already decided and filled. It never
recomputes a signal, stop, target, fill, fee, size or same-bar rule. A field
that is missing or unusable raises :class:`ReplayUnsupported`.

Fields read per closed trade:

- ``outcome`` (verbatim; ``OPEN``/empty rows are not closed and are skipped)
- ``direction``
- ``entry_index``; for a ``CAUSAL_NEXT_OPEN`` row, ``fill_index`` instead (the
  engine keeps the signal bar in ``entry_index`` and fills on ``fill_index``)
- ``exit_index``
- ``entry_price``, ``exit_price``, ``slippage_rate``. With ``slippage_rate == 0``
  the prices are the fills. With ``slippage_rate > 0`` (COMBO_SD_* rows) the
  fills are the ones ``enrich_trade_execution`` charges fees on:
  LONG entry x (1 + s), exit x (1 - s); SHORT entry x (1 - s), exit x (1 + s).
  They are cross-checked against ``notional_entry_usd / qty`` and
  ``notional_exit_usd / qty``. The same function computes gross P&L from
  these slipped fills too, so the engine's own ``r_net`` and a replay of its
  fills agree (before that fee-helper fix they showed a ``net_r`` mismatch).
- ``qty``, ``fee_entry_rate``, ``fee_exit_rate`` (fractions of notional)
- ``risk_usd`` (realized risk after the leverage cap: the ``r_net`` denominator)
- ``stop_price``; target = ``tp<n>`` for a ``TP<n>`` outcome, else ``tp1``
- ``r_net``

Pass-through by construction (labels VectorBT never computes): stop, target,
outcome. VectorBT independently reproduces the bar indices, the fills, the
one-position-at-a-time constraint and net R from its own fills and fees.

Research only. No paper or live path calls this module.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from app.research.vectorbt_replay import ReplayOrder

try:  # one exception type for both adapters when the engine module is present
    from app.research.vectorbt_orders_engine import ReplayUnsupported
except Exception:  # noqa: BLE001

    class ReplayUnsupported(Exception):  # type: ignore[no-redef]
        """The trades cannot be replayed as explicit orders (reason in message)."""


CAUSAL_NEXT_OPEN = "CAUSAL_NEXT_OPEN"  # causal_execution.CAUSAL_NEXT_OPEN
_NOT_CLOSED = ("", "OPEN", "None")
# notional / qty versus the slipped fill: float round trip only.
_FILL_XCHECK_REL_TOL = 1e-9


def _num(trade: Mapping[str, Any], key: str, where: str, *, positive: bool = False) -> float:
    if key not in trade or trade.get(key) is None or trade.get(key) == "":
        raise ReplayUnsupported(f"{where}: missing field {key!r}")
    try:
        v = float(trade[key])
    except (TypeError, ValueError) as exc:
        raise ReplayUnsupported(f"{where}: field {key!r}={trade[key]!r} is not numeric") from exc
    if not math.isfinite(v):
        raise ReplayUnsupported(f"{where}: field {key!r} is not finite")
    if positive and v <= 0:
        raise ReplayUnsupported(f"{where}: field {key!r}={v!r} must be > 0")
    return v


def _idx(trade: Mapping[str, Any], key: str, where: str) -> int:
    v = trade.get(key)
    if v is None or v == "" or isinstance(v, bool):
        raise ReplayUnsupported(f"{where}: missing field {key!r}")
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise ReplayUnsupported(f"{where}: field {key!r}={v!r} is not an index") from exc
    if not f.is_integer() or f < 0:
        raise ReplayUnsupported(f"{where}: field {key!r}={v!r} is not an index")
    return int(f)


def _opt_price(trade: Mapping[str, Any], key: str) -> float | None:
    v = trade.get(key)
    if v is None or v == "":
        return None
    return float(v)


def _target_label(trade: Mapping[str, Any], outcome: str) -> float | None:
    if len(outcome) == 3 and outcome.startswith("TP") and outcome[2] in "123":
        hit = _opt_price(trade, f"tp{outcome[2]}")
        if hit is not None:
            return hit
    return _opt_price(trade, "tp1")


def is_closed(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("outcome") or "") not in _NOT_CLOSED


def _fills(trade: Mapping[str, Any], direction: str, qty: float, where: str) -> tuple[float, float]:
    entry = _num(trade, "entry_price", where, positive=True)
    exit_px = _num(trade, "exit_price", where, positive=True)
    if "slippage_rate" not in trade:
        raise ReplayUnsupported(f"{where}: missing field 'slippage_rate'")
    slip = _num(trade, "slippage_rate", where)
    if slip < 0:
        raise ReplayUnsupported(f"{where}: slippage_rate={slip!r} < 0")
    if slip == 0:
        return entry, exit_px
    if direction == "SHORT":
        fill_entry, fill_exit = entry * (1.0 - slip), exit_px * (1.0 + slip)
    else:
        fill_entry, fill_exit = entry * (1.0 + slip), exit_px * (1.0 - slip)
    for key, fill in (("notional_entry_usd", fill_entry), ("notional_exit_usd", fill_exit)):
        notional = _num(trade, key, where, positive=True)
        if not math.isclose(notional / qty, fill, rel_tol=_FILL_XCHECK_REL_TOL, abs_tol=0.0):
            raise ReplayUnsupported(
                f"{where}: {key}/qty={notional / qty!r} disagrees with the slipped fill {fill!r}"
            )
    return fill_entry, fill_exit


def combo_order(combination_id: str, trade: Mapping[str, Any], position: int) -> ReplayOrder:
    """One closed COMBO trade dict -> one ReplayOrder (raises ReplayUnsupported)."""
    tag = trade.get("trade_no") or trade.get("trade_id") or position
    where = f"{combination_id} trade {tag}"
    outcome = str(trade.get("outcome") or "")
    if outcome in _NOT_CLOSED:
        raise ReplayUnsupported(f"{where}: not closed (outcome={outcome!r})")
    direction = str(trade.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        raise ReplayUnsupported(f"{where}: direction={trade.get('direction')!r}")
    causal = str(trade.get("execution_model") or "") == CAUSAL_NEXT_OPEN
    entry_index = _idx(trade, "fill_index" if causal else "entry_index", where)
    exit_index = _idx(trade, "exit_index", where)
    if exit_index < entry_index:
        raise ReplayUnsupported(f"{where}: exit_index {exit_index} < entry bar {entry_index}")
    qty = _num(trade, "qty", where, positive=True)
    entry_fill, exit_fill = _fills(trade, direction, qty, where)
    fee_in = _num(trade, "fee_entry_rate", where)
    fee_out = _num(trade, "fee_exit_rate", where)
    if fee_in < 0 or fee_out < 0:
        raise ReplayUnsupported(f"{where}: negative fee rate")
    risk = _num(trade, "risk_usd", where, positive=True)
    net_r = trade.get("r_net")
    return ReplayOrder(
        trade_key=f"{tag}@{entry_index}",
        direction=direction,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_price=entry_fill,
        exit_price=exit_fill,
        qty=qty,
        entry_fee_rate=fee_in,
        exit_fee_rate=fee_out,
        risk_usd=risk,
        stop=_num(trade, "stop_price", where),
        target=_target_label(trade, outcome),
        outcome=outcome,
        net_r=float(net_r) if net_r is not None else None,
    )


def combo_replay_orders(combination_id: str, trades: Sequence[Mapping[str, Any]]) -> list[ReplayOrder]:
    """Closed trades of one strategy_matrix row -> ReplayOrders, in row order.

    Rows whose outcome is OPEN (or empty) are not closed trades and are skipped.
    Any closed trade that cannot be mapped raises ReplayUnsupported.
    """
    if trades is None:
        raise ReplayUnsupported(f"{combination_id}: row has no 'trades' list")
    return [combo_order(combination_id, t, i) for i, t in enumerate(trades) if is_closed(t)]


__all__ = ["ReplayUnsupported", "combo_order", "combo_replay_orders", "is_closed"]
