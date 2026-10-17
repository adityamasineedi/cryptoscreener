"""Engine-family trades -> VectorBT replay orders (parity check only).

Maps the closed trades that ``engine_backtest.run_engine_cell`` returns (the
same dicts the Backtest screen row carries in ``trades``) to
:class:`~app.research.vectorbt_replay.ReplayOrder`. It only reads what each
engine already decided and filled; it never recomputes a signal, stop, target,
fill, fee, slippage, size or same-bar rule. A field that is missing or not
usable raises :class:`ReplayUnsupported` with the reason instead of being
guessed.

Fields read per family (all from the trade dict):

- multicap (``combination_backtest.ResearchTrade.to_dict`` + ``trade_fees``):
  entry_index, exit_index, entry_price, exit_price, qty, fee_entry_rate,
  fee_exit_rate, risk_usd, stop_price, tp1, outcome, r_net.
- candle12 (CANDLE12_V2 via ``engine_backtest._run_candle12``): same keys as
  multicap. ``entry_price`` is the engine's slipped C1-trigger fill and
  ``exit_price`` is already ``c12._exit_fill(...)`` (``raw_exit_price`` keeps the
  unslipped level), so both are fills.
- ohlcv_structure_experiment (``engine_backtest._run_ose`` + ``trade_fees``):
  same keys as multicap; entry_price is the slipped next-bar open, exit_price
  the engine's exit fill.
- candle12_v3 (built inside ``candle12_v3.run_candle12_v3``, not trade_fees):
  entry_index, legs[0].bar (== exit_index), entry_price (slipped fill),
  legs[0].fill (slipped exit fill), qty, risk_usd (qty x |entry - initial stop|),
  stop_price (initial stop), tp1, outcome, r_net. Fee rate: the engine charges
  ``cfg.taker_fee`` on both legs and does not store the rate on the trade, so
  the caller passes it as ``taker_fee`` (the request's taker rate that
  ``engine_backtest`` hands to ``Candle12V3Config``); without it the rate is
  read back from fee_entry_usd / (qty x entry_price) and must agree with the
  exit leg. Multi-leg trades (``tp1_runner``) are not replayable as one order.

stop, target and outcome are labels carried with the order; VectorBT never
computes them, so they match by construction. Only entry/exit bar and price
and net R are reproduced by VectorBT's own order simulation.

Research only. No paper or live path calls this module.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from app.research.vectorbt_replay import ReplayOrder

TRADE_FEES_FAMILIES = ("multicap", "candle12", "ohlcv_structure_experiment")
V3_FAMILY = "candle12_v3"


class ReplayUnsupported(Exception):
    """The engine's trades cannot be replayed as explicit orders (reason in message)."""


def _family(strategy_id: str) -> str:
    from app.research.engine_backtest import ENGINE_STRATEGIES, engine_strategy_id

    sid = engine_strategy_id(strategy_id)
    if sid is None:
        raise ReplayUnsupported(f"{strategy_id!r} is not an engine-family strategy")
    return str(ENGINE_STRATEGIES[sid]["family"])


def _key(trade: Mapping[str, Any], pos: int) -> str:
    for name in ("trade_id", "trade_no", "entry_index"):
        v = trade.get(name)
        if v is not None and v != "":
            return str(v)
    return f"pos{pos}"


def _num(trade: Mapping[str, Any], name: str, key: str, *, positive: bool = True) -> float:
    v = trade.get(name)
    if v is None or isinstance(v, bool):
        raise ReplayUnsupported(f"{key}: missing {name}")
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise ReplayUnsupported(f"{key}: {name}={v!r} not numeric") from exc
    if not math.isfinite(f) or (positive and f <= 0):
        raise ReplayUnsupported(f"{key}: {name}={v!r}")
    return f


def _opt_num(trade: Mapping[str, Any], name: str) -> float | None:
    v = trade.get(name)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _index(trade: Mapping[str, Any], name: str, key: str) -> int:
    v = trade.get(name)
    if v is None or isinstance(v, bool):
        raise ReplayUnsupported(f"{key}: missing {name} (no bar index on the trade)")
    try:
        i = int(v)
    except (TypeError, ValueError) as exc:
        raise ReplayUnsupported(f"{key}: {name}={v!r}") from exc
    if i != v and not (isinstance(v, float) and float(i) == v):
        raise ReplayUnsupported(f"{key}: {name}={v!r} not an integer")
    if i < 0:
        raise ReplayUnsupported(f"{key}: {name}={i}")
    return i


def _direction(trade: Mapping[str, Any], key: str) -> str:
    d = str(trade.get("direction") or "").upper()
    if d not in ("LONG", "SHORT"):
        raise ReplayUnsupported(f"{key}: direction {trade.get('direction')!r}")
    return d


def _outcome(trade: Mapping[str, Any], key: str) -> str:
    o = trade.get("outcome")
    if o is None or str(o) in ("", "OPEN", "None"):
        raise ReplayUnsupported(f"{key}: trade not closed (outcome={o!r})")
    return str(o)


def _common(trade: Mapping[str, Any], pos: int) -> dict[str, Any]:
    key = _key(trade, pos)
    entry_index = _index(trade, "entry_index", key)
    return {
        "trade_key": key,
        "direction": _direction(trade, key),
        "entry_index": entry_index,
        "entry_price": _num(trade, "entry_price", key),
        "qty": _num(trade, "qty", key),
        "risk_usd": _num(trade, "risk_usd", key),
        "stop": _opt_num(trade, "stop_price"),
        "target": _opt_num(trade, "tp1"),
        "outcome": _outcome(trade, key),
        "net_r": _opt_num(trade, "r_net"),
    }


def _order_trade_fees(trade: Mapping[str, Any], pos: int) -> ReplayOrder:
    """multicap / candle12 / OSE: trades sized and charged by trade_fees."""
    base = _common(trade, pos)
    key = base["trade_key"]
    slip = _opt_num(trade, "slippage_rate") or 0.0
    if slip != 0.0:
        # trade_fees then prices gross PnL on the unslipped entry but the fee on
        # the slipped fill; no single entry fill reproduces both.
        raise ReplayUnsupported(f"{key}: trade_fees slippage_rate={slip} (gross and fee use different entry prices)")
    return ReplayOrder(
        **base,
        exit_index=_index(trade, "exit_index", key),
        exit_price=_num(trade, "exit_price", key),
        entry_fee_rate=_num(trade, "fee_entry_rate", key, positive=False),
        exit_fee_rate=_num(trade, "fee_exit_rate", key, positive=False),
    )


def _v3_rate(trade: Mapping[str, Any], key: str, entry: float, exit_fill: float, qty: float) -> float:
    fe = _num(trade, "fee_entry_usd", key, positive=False)
    fx = _num(trade, "fee_exit_usd", key, positive=False)
    rate = -fe / (qty * entry)
    rate_x = -fx / (qty * exit_fill)
    if not math.isclose(rate, rate_x, rel_tol=1e-9, abs_tol=1e-15):
        raise ReplayUnsupported(f"{key}: entry fee rate {rate} != exit fee rate {rate_x}")
    return rate


def _order_v3(trade: Mapping[str, Any], pos: int, taker_fee: float | None) -> ReplayOrder:
    base = _common(trade, pos)
    key = base["trade_key"]
    legs = trade.get("legs")
    if not isinstance(legs, Sequence) or not legs:
        raise ReplayUnsupported(f"{key}: candle12_v3 trade without exit legs")
    if len(legs) != 1:
        raise ReplayUnsupported(
            f"{key}: candle12_v3 trade has {len(legs)} exit legs (partial exits are not one order)"
        )
    leg = legs[0]
    exit_index = _index(leg, "bar", key)
    if trade.get("exit_index") is not None and _index(trade, "exit_index", key) != exit_index:
        raise ReplayUnsupported(f"{key}: exit leg bar {exit_index} != exit_index {trade.get('exit_index')}")
    exit_fill = _num(leg, "fill", key)
    leg_qty = _num(leg, "qty", key)
    if leg_qty != base["qty"]:
        raise ReplayUnsupported(f"{key}: exit leg qty {leg_qty} != position qty {base['qty']}")
    if _opt_num(trade, "funding_usd"):
        raise ReplayUnsupported(f"{key}: funding_usd={trade.get('funding_usd')} is outside the order replay")
    if taker_fee is not None:
        rate = float(taker_fee)
    else:
        rate = _v3_rate(trade, key, base["entry_price"], exit_fill, base["qty"])
    return ReplayOrder(
        **base,
        exit_index=exit_index,
        exit_price=exit_fill,
        entry_fee_rate=rate,
        exit_fee_rate=rate,
    )


def engine_replay_orders(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    taker_fee: float | None = None,
) -> list[ReplayOrder]:
    """One ReplayOrder per closed engine trade, read from the engine's own fields.

    ``trades`` are ``run_engine_cell(...)["trades"]``. ``taker_fee`` is used only
    for CANDLE12_V3 (see module docstring); other families carry their per-leg
    rates on the trade.
    """
    family = _family(strategy_id)
    out: list[ReplayOrder] = []
    for pos, t in enumerate(trades):
        if family in TRADE_FEES_FAMILIES:
            out.append(_order_trade_fees(t, pos))
        elif family == V3_FAMILY:
            out.append(_order_v3(t, pos, taker_fee))
        else:  # pragma: no cover - new family
            raise ReplayUnsupported(f"no replay mapping for engine family {family!r}")
    return out


__all__ = ["ReplayUnsupported", "engine_replay_orders"]
