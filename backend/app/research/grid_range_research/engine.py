"""Regime-gated non-martingale grid simulator (research-only).

Architecture:
- 1H: regime gate, range bounds, breakout kill
- 15M: level touches, inventory, entries/exits
- Fees/slippage via research blotter defaults (same constants as COMBO_02_V2 path)
- Does NOT call or modify COMBO_02_V2 evaluate/playbooks/risk_finalize
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.grid_range_research.config import (
    GRID_KILL_REGIMES,
    SLIPPAGE_RATE,
    GridVariantConfig,
)
from app.research.grid_range_research.levels import build_grid_levels, grid_spacing
from app.research.grid_range_research.risk import GridRiskCaps, apply_fees_and_sizing
from app.research.trade_fees import DEFAULT_TAKER_FEE
from app.signals._candle_utils import candle_time


def _aware(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        # ms or s
        v = float(ts)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ohlc(c: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(c["open"]),
        float(c["high"]),
        float(c["low"]),
        float(c["close"]),
    )


def _apply_slippage(price: float, *, direction: str, side: str, rate: float) -> float:
    """Adverse slippage: buy higher, sell lower."""
    r = float(rate)
    if side == "entry":
        if direction == "LONG":
            return price * (1.0 + r)
        return price * (1.0 - r)
    # exit
    if direction == "LONG":
        return price * (1.0 - r)
    return price * (1.0 + r)


@dataclass
class OpenGridPosition:
    direction: str
    level_index: int
    entry_price: float
    stop_price: float
    target_price: float
    entry_time: str
    entry_15m_index: int
    risk_unit: float
    regime_at_entry: str
    session_id: str


@dataclass
class GridSession:
    session_id: str
    symbol: str
    variant_id: str
    start_1h_index: int
    start_time: str
    regime_start: str
    range_low: float
    range_high: float
    n_levels: int
    spacing: float
    levels: list[float]
    end_1h_index: int | None = None
    end_time: str | None = None
    stop_reason: str | None = None
    max_inventory: int = 0
    max_exposure_usd: float = 0.0
    total_fees: float = 0.0
    total_r_net: float = 0.0
    n_cycles: int = 0
    breakout_seen: bool = False
    inventory_at_breakout: int = 0
    allow_new_long: bool = True
    allow_new_short: bool = True
    allow_new_entries: bool = True


@dataclass
class GridBacktestResult:
    symbol: str
    variant_id: str
    cycles: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    risk_summary: dict[str, Any] = field(default_factory=dict)
    funding_status: str = "FUNDING_NOT_MODELED"


def _build_1h_to_15m_map(
    candles_1h: Sequence[Mapping[str, Any]],
    candles_15m: Sequence[Mapping[str, Any]],
) -> list[list[int]]:
    """For each 1H bar, list of 15M indices whose open is within that hour."""
    out: list[list[int]] = [[] for _ in range(len(candles_1h))]
    if not candles_1h or not candles_15m:
        return out
    j = 0
    n15 = len(candles_15m)
    for i, c1 in enumerate(candles_1h):
        t0 = _aware(candle_time(c1))
        if t0 is None:
            continue
        # advance j to first 15m >= hour open
        while j < n15:
            tj = _aware(candle_time(candles_15m[j]))
            if tj is None or tj < t0:
                j += 1
                continue
            break
        k = j
        # next hour open
        t1 = None
        if i + 1 < len(candles_1h):
            t1 = _aware(candle_time(candles_1h[i + 1]))
        while k < n15:
            tk = _aware(candle_time(candles_15m[k]))
            if tk is None:
                k += 1
                continue
            if t1 is not None and tk >= t1:
                break
            # still within hour if no next bar: accept all remaining after t0
            if tk < t0:
                k += 1
                continue
            out[i].append(k)
            k += 1
        j = k
    return out


def _precompute_regimes_and_bounds(
    candles_1h: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    index_start: int,
) -> tuple[list[str], list[tuple[float, float] | None]]:
    """Causal regime/bounds via research fast-path (same classifier, O(n))."""
    from app.research.grid_range_research.fast_structure import (
        precompute_regimes_and_bounds_fast,
    )

    if not candles_1h:
        raise AssertionError("empty_candles_for_regime_precompute")
    return precompute_regimes_and_bounds_fast(
        candles_1h, symbol=symbol, index_start=index_start
    )


def run_grid_backtest(
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_15m: Sequence[Mapping[str, Any]],
    variant: GridVariantConfig,
    *,
    index_start: int | None = None,
    slippage_rate: float = SLIPPAGE_RATE,
    risk_caps: GridRiskCaps | None = None,
    precomputed_regimes: list[str] | None = None,
    precomputed_bounds: list[tuple[float, float] | None] | None = None,
    precomputed_map_15: list[list[int]] | None = None,
) -> GridBacktestResult:
    """Simulate one grid variant on one symbol."""
    caps = risk_caps or GridRiskCaps(max_simultaneous=variant.max_simultaneous)
    # Cap must match variant inventory limit
    caps = GridRiskCaps(
        risk_usd_per_position=caps.risk_usd_per_position,
        max_simultaneous=variant.max_simultaneous,
        account_equity=caps.account_equity,
        leverage=caps.leverage,
        taker_fee=caps.taker_fee,
        maker_fee=caps.maker_fee,
    )

    idx0 = int(index_start or 0)
    if precomputed_regimes is not None and precomputed_bounds is not None:
        regimes, bounds = precomputed_regimes, precomputed_bounds
    else:
        regimes, bounds = _precompute_regimes_and_bounds(
            candles_1h, symbol=symbol, index_start=idx0
        )
    map_15 = (
        precomputed_map_15
        if precomputed_map_15 is not None
        else _build_1h_to_15m_map(candles_1h, candles_15m)
    )

    cycles: list[dict[str, Any]] = []
    sessions_out: list[dict[str, Any]] = []
    open_pos: list[OpenGridPosition] = []
    session: GridSession | None = None
    # Per-level open flags to prevent duplicate fills while a leg is live
    long_open_at: set[int] = set()
    short_open_at: set[int] = set()
    session_counter = 0
    max_inventory_global = 0
    max_exposure_global = 0.0
    max_mae_r_global = 0.0

    def _inventory() -> int:
        return len(open_pos)

    def _exposure_usd() -> float:
        # Each position sized to risk_usd; exposure ≈ sum notional approx risk*entry/spacing
        exp = 0.0
        for p in open_pos:
            if p.risk_unit > 0:
                qty = caps.risk_usd_per_position / p.risk_unit
                exp += abs(qty * p.entry_price)
        return exp

    def _close_position(
        pos: OpenGridPosition,
        *,
        exit_raw: float,
        exit_time: str,
        exit_15m_index: int,
        outcome: str,
        reason: str,
    ) -> None:
        nonlocal max_mae_r_global
        direction = pos.direction
        exit_px = _apply_slippage(
            exit_raw, direction=direction, side="exit", rate=slippage_rate
        )
        risk = abs(pos.entry_price - pos.stop_price)
        if risk <= 0:
            r_gross = 0.0
        elif direction == "LONG":
            r_gross = (exit_px - pos.entry_price) / risk
        else:
            r_gross = (pos.entry_price - exit_px) / risk

        # MAE proxy from adverse move vs entry toward stop (bounded)
        if direction == "LONG":
            mae_r = max(0.0, (pos.entry_price - exit_px) / risk) if risk > 0 else 0.0
        else:
            mae_r = max(0.0, (exit_px - pos.entry_price) / risk) if risk > 0 else 0.0
        max_mae_r_global = max(max_mae_r_global, mae_r)

        priced = apply_fees_and_sizing(
            direction=direction,
            entry_price=pos.entry_price,
            stop_price=pos.stop_price,
            exit_price=exit_px,
            r_gross=r_gross,
            entry_type="MARKET",
            caps=caps,
        )
        fee = float(priced.get("total_fee") or 0.0)
        r_net = priced.get("r_net")
        if r_net is None:
            r_net = r_gross
        r_net_f = float(r_net)

        row = {
            "symbol": symbol,
            "variant_id": variant.variant_id,
            "session_id": pos.session_id,
            "direction": direction,
            "entry_time": pos.entry_time,
            "exit_time": exit_time,
            "entry_15m_index": pos.entry_15m_index,
            "exit_15m_index": exit_15m_index,
            "entry_price": pos.entry_price,
            "exit_price": exit_px,
            "stop_price": pos.stop_price,
            "target_price": pos.target_price,
            "level_index": pos.level_index,
            "regime": pos.regime_at_entry,
            "outcome": outcome,
            "exit_reason": reason,
            "r_gross": r_gross,
            "r_multiple": r_net_f,  # primary research R = net after fees
            "r_net": r_net_f,
            "fees": fee,
            "qty": priced.get("quantity"),
            "gross_pnl": priced.get("gross_pnl"),
            "net_pnl": priced.get("net_pnl"),
            "risk_usd": priced.get("risk_usd") or caps.risk_usd_per_position,
            "taker_fee_rate": DEFAULT_TAKER_FEE,
            "slippage_rate": slippage_rate,
            "year": str(exit_time)[:4] if exit_time else None,
        }
        cycles.append(row)
        if session is not None and session.session_id == pos.session_id:
            session.total_fees += fee
            session.total_r_net += r_net_f
            session.n_cycles += 1

        if direction == "LONG":
            long_open_at.discard(pos.level_index)
        else:
            short_open_at.discard(pos.level_index)
        open_pos.remove(pos)

    def _flatten_all(*, exit_raw: float, exit_time: str, exit_15m: int, reason: str) -> None:
        for pos in list(open_pos):
            _close_position(
                pos,
                exit_raw=exit_raw,
                exit_time=exit_time,
                exit_15m_index=exit_15m,
                outcome="SESSION_KILL",
                reason=reason,
            )

    def _end_session(i1h: int, reason: str, exit_raw: float | None = None) -> None:
        nonlocal session
        if session is None:
            return
        t_end = _aware(candle_time(candles_1h[i1h]))
        end_s = t_end.isoformat() if t_end else None
        # Flatten any leftover inventory
        if open_pos:
            px = float(candles_1h[i1h]["close"]) if exit_raw is None else float(exit_raw)
            # use last 15m of this hour if available
            idxs = map_15[i1h] if i1h < len(map_15) else []
            exit_15 = idxs[-1] if idxs else 0
            et = end_s or ""
            if idxs:
                t15 = _aware(candle_time(candles_15m[exit_15]))
                if t15:
                    et = t15.isoformat()
            _flatten_all(
                exit_raw=px, exit_time=et, exit_15m=exit_15, reason=reason
            )
        session.end_1h_index = i1h
        session.end_time = end_s
        session.stop_reason = reason
        sessions_out.append(
            {
                "session_id": session.session_id,
                "symbol": session.symbol,
                "variant_id": session.variant_id,
                "start_timestamp": session.start_time,
                "end_timestamp": session.end_time,
                "regime": session.regime_start,
                "range_high": session.range_high,
                "range_low": session.range_low,
                "grid_spacing": session.spacing,
                "number_of_levels": session.n_levels,
                "number_of_positions": session.n_cycles,
                "maximum_inventory": session.max_inventory,
                "maximum_exposure": session.max_exposure_usd,
                "total_fees": session.total_fees,
                "R_result": session.total_r_net,
                "reason_grid_stopped": session.stop_reason,
                "breakout_seen": session.breakout_seen,
                "inventory_at_breakout": session.inventory_at_breakout,
            }
        )
        session = None
        long_open_at.clear()
        short_open_at.clear()

    def _try_start_session(i1h: int, regime: str) -> None:
        nonlocal session, session_counter
        if session is not None:
            return
        if regime not in variant.enabled_regimes:
            return
        b = bounds[i1h]
        if b is None:
            return
        rl, rh = b
        try:
            levels = build_grid_levels(rl, rh, variant.n_levels)
            spacing = grid_spacing(rl, rh, variant.n_levels)
        except ValueError:
            return
        if spacing <= 0:
            return
        session_counter += 1
        t0 = _aware(candle_time(candles_1h[i1h]))
        session = GridSession(
            session_id=f"{symbol}-{variant.variant_id}-{session_counter}",
            symbol=symbol,
            variant_id=variant.variant_id,
            start_1h_index=i1h,
            start_time=t0.isoformat() if t0 else "",
            regime_start=regime,
            range_low=rl,
            range_high=rh,
            n_levels=variant.n_levels,
            spacing=spacing,
            levels=levels,
        )

    def _open_long(level_i: int, fill_raw: float, t_iso: str, j15: int, regime: str) -> None:
        assert session is not None
        if not session.allow_new_entries or not session.allow_new_long:
            return
        if _inventory() >= variant.max_simultaneous:
            return
        if level_i in long_open_at:
            return
        if level_i >= len(session.levels) - 1:
            return
        # Only long below mid (mean-reversion inventory)
        mid_i = (len(session.levels) - 1) / 2.0
        if level_i > mid_i:
            return
        spacing = session.spacing
        entry = _apply_slippage(
            fill_raw, direction="LONG", side="entry", rate=slippage_rate
        )
        target = session.levels[level_i + 1]
        stop = entry - spacing
        if stop <= 0 or abs(entry - stop) <= 0:
            return
        pos = OpenGridPosition(
            direction="LONG",
            level_index=level_i,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            entry_time=t_iso,
            entry_15m_index=j15,
            risk_unit=abs(entry - stop),
            regime_at_entry=regime,
            session_id=session.session_id,
        )
        open_pos.append(pos)
        long_open_at.add(level_i)
        session.max_inventory = max(session.max_inventory, _inventory())
        session.max_exposure_usd = max(session.max_exposure_usd, _exposure_usd())

    def _open_short(level_i: int, fill_raw: float, t_iso: str, j15: int, regime: str) -> None:
        assert session is not None
        if not session.allow_new_entries or not session.allow_new_short:
            return
        if _inventory() >= variant.max_simultaneous:
            return
        if level_i in short_open_at:
            return
        if level_i <= 0:
            return
        mid_i = (len(session.levels) - 1) / 2.0
        if level_i < mid_i:
            return
        spacing = session.spacing
        entry = _apply_slippage(
            fill_raw, direction="SHORT", side="entry", rate=slippage_rate
        )
        target = session.levels[level_i - 1]
        stop = entry + spacing
        if abs(entry - stop) <= 0:
            return
        pos = OpenGridPosition(
            direction="SHORT",
            level_index=level_i,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            entry_time=t_iso,
            entry_15m_index=j15,
            risk_unit=abs(entry - stop),
            regime_at_entry=regime,
            session_id=session.session_id,
        )
        open_pos.append(pos)
        short_open_at.add(level_i)
        session.max_inventory = max(session.max_inventory, _inventory())
        session.max_exposure_usd = max(session.max_exposure_usd, _exposure_usd())

    # Main 1H walk
    for i in range(idx0, len(candles_1h)):
        regime = regimes[i]
        o1, h1, l1, c1 = _ohlc(candles_1h[i])

        # Start session if idle and enabled
        if session is None:
            _try_start_session(i, regime)
            if session is None:
                continue

        # Regime kill — stop new entries; flatten
        if regime in GRID_KILL_REGIMES:
            session.allow_new_entries = False
            _end_session(i, reason=f"REGIME_KILL:{regime}", exit_raw=c1)
            continue

        # Optional: if still in enabled regime but LVC-only path etc. — already gated at start
        if regime not in variant.enabled_regimes:
            # Left primary grid regimes without being a hard kill (shouldn't happen often)
            session.allow_new_entries = False
            _end_session(i, reason=f"REGIME_EXIT:{regime}", exit_raw=c1)
            continue

        # Structural range break on 1H close
        assert session is not None
        if c1 > session.range_high:
            if not session.breakout_seen:
                session.breakout_seen = True
                session.inventory_at_breakout = _inventory()
            session.allow_new_short = False
            # Clear trend transition → kill session
            if regime in GRID_KILL_REGIMES or c1 > session.range_high + session.spacing:
                session.allow_new_entries = False
                _end_session(i, reason="RANGE_BREAK_UPPER", exit_raw=c1)
                continue
        if c1 < session.range_low:
            if not session.breakout_seen:
                session.breakout_seen = True
                session.inventory_at_breakout = _inventory()
            session.allow_new_long = False
            if regime in GRID_KILL_REGIMES or c1 < session.range_low - session.spacing:
                session.allow_new_entries = False
                _end_session(i, reason="RANGE_BREAK_LOWER", exit_raw=c1)
                continue

        # Execute on 15M bars belonging to this 1H bar
        for j15 in map_15[i]:
            bar = candles_15m[j15]
            o, h, l, c = _ohlc(bar)
            t15 = _aware(candle_time(bar))
            t_iso = t15.isoformat() if t15 else ""

            # Manage exits first (conservative: SL before TP if both in bar)
            for pos in list(open_pos):
                if pos.direction == "LONG":
                    hit_sl = l <= pos.stop_price
                    hit_tp = h >= pos.target_price
                    if hit_sl and hit_tp:
                        _close_position(
                            pos,
                            exit_raw=pos.stop_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="SL",
                            reason="SL_BEFORE_TP_AMBIGUOUS",
                        )
                    elif hit_sl:
                        _close_position(
                            pos,
                            exit_raw=pos.stop_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="SL",
                            reason="STOP",
                        )
                    elif hit_tp:
                        _close_position(
                            pos,
                            exit_raw=pos.target_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="TP",
                            reason="TARGET_NEXT_LEVEL",
                        )
                else:
                    hit_sl = h >= pos.stop_price
                    hit_tp = l <= pos.target_price
                    if hit_sl and hit_tp:
                        _close_position(
                            pos,
                            exit_raw=pos.stop_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="SL",
                            reason="SL_BEFORE_TP_AMBIGUOUS",
                        )
                    elif hit_sl:
                        _close_position(
                            pos,
                            exit_raw=pos.stop_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="SL",
                            reason="STOP",
                        )
                    elif hit_tp:
                        _close_position(
                            pos,
                            exit_raw=pos.target_price,
                            exit_time=t_iso,
                            exit_15m_index=j15,
                            outcome="TP",
                            reason="TARGET_NEXT_LEVEL",
                        )

            if session is None or not session.allow_new_entries:
                continue

            # Entries on level touches (only if level actually touched)
            for li, level_px in enumerate(session.levels):
                if not (l <= level_px <= h):
                    continue
                # Prefer mean-reversion sides
                mid_i = (len(session.levels) - 1) / 2.0
                if li <= mid_i:
                    _open_long(li, level_px, t_iso, j15, regime)
                if li >= mid_i:
                    _open_short(li, level_px, t_iso, j15, regime)

            inv = _inventory()
            max_inventory_global = max(max_inventory_global, inv)
            exp = _exposure_usd()
            max_exposure_global = max(max_exposure_global, exp)
            if session is not None:
                session.max_inventory = max(session.max_inventory, inv)
                session.max_exposure_usd = max(session.max_exposure_usd, exp)

    # End of data — close open session
    if session is not None:
        last = len(candles_1h) - 1
        _end_session(last, reason="END_OF_DATA", exit_raw=float(candles_1h[last]["close"]))

    return GridBacktestResult(
        symbol=symbol,
        variant_id=variant.variant_id,
        cycles=cycles,
        sessions=sessions_out,
        risk_summary={
            "risk_per_grid_position_usd": caps.risk_usd_per_position,
            "max_simultaneous_positions": variant.max_simultaneous,
            "max_simultaneous_risk_usd": caps.max_simultaneous_risk_usd,
            "max_total_grid_exposure_usd_observed": max_exposure_global,
            "max_inventory_observed": max_inventory_global,
            "max_adverse_excursion_r_observed": max_mae_r_global,
            "account_equity": caps.account_equity,
            "leverage": caps.leverage,
            "taker_fee": caps.taker_fee,
            "maker_fee": caps.maker_fee,
            "slippage_rate": slippage_rate,
        },
        funding_status="FUNDING_NOT_MODELED",
    )
