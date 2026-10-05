"""Trade-to-bar attribution for market-structure analytics (read-only).

Uses the authoritative strategy trade ledger. Never creates or removes trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _aware(raw)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _aware(datetime.fromisoformat(s))
    except ValueError:
        return None


def trade_id_of(trade: Mapping[str, Any], fallback_index: int) -> str:
    for key in ("trade_no", "trade_id"):
        v = trade.get(key)
        if v is not None and str(v).strip() != "":
            return str(v)
    ei = trade.get("entry_index")
    if ei is not None:
        return str(ei)
    return f"idx_{fallback_index}"


@dataclass
class LedgerTrade:
    trade_id: str
    trade: Mapping[str, Any]
    entry_index: int | None
    exit_index: int | None
    signal_time: datetime | None
    entry_time: datetime | None
    exit_time: datetime | None


def normalize_ledger_trades(
    trades: Sequence[Mapping[str, Any]] | None,
) -> list[LedgerTrade]:
    out: list[LedgerTrade] = []
    for i, t in enumerate(trades or []):
        ei = t.get("entry_index")
        try:
            entry_index = int(ei) if ei is not None else None
        except (TypeError, ValueError):
            entry_index = None
        xi = t.get("exit_index")
        try:
            exit_index = int(xi) if xi is not None else None
        except (TypeError, ValueError):
            exit_index = None
        signal_time = parse_ts(t.get("signal_time"))
        # COMBO_02 fills on the signal bar; entry_time defaults to signal_time.
        entry_time = parse_ts(t.get("entry_time")) or signal_time
        exit_time = parse_ts(t.get("exit_time"))
        out.append(
            LedgerTrade(
                trade_id=trade_id_of(t, i),
                trade=t,
                entry_index=entry_index,
                exit_index=exit_index,
                signal_time=signal_time,
                entry_time=entry_time,
                exit_time=exit_time,
            )
        )
    return out


def build_index_maps(
    ledger: Sequence[LedgerTrade],
) -> tuple[dict[int, LedgerTrade], dict[int, list[LedgerTrade]], dict[int, LedgerTrade]]:
    """Return (execution_by_entry_index, active_by_bar, exit_by_index)."""
    by_entry: dict[int, LedgerTrade] = {}
    by_exit: dict[int, LedgerTrade] = {}
    active: dict[int, list[LedgerTrade]] = {}
    for lt in ledger:
        if lt.entry_index is not None:
            # First write wins; duplicates flagged later in quality.
            by_entry.setdefault(lt.entry_index, lt)
        if lt.exit_index is not None:
            by_exit.setdefault(lt.exit_index, lt)
        if lt.entry_index is None:
            continue
        start = lt.entry_index
        # Position is active after the execution bar until exit inclusive for exit role.
        end = lt.exit_index if lt.exit_index is not None else start
        for i in range(start, end + 1):
            active.setdefault(i, []).append(lt)
    return by_entry, active, by_exit


def attribute_bar(
    bar_index: int,
    *,
    by_entry: Mapping[int, LedgerTrade],
    active: Mapping[int, Sequence[LedgerTrade]],
    by_exit: Mapping[int, LedgerTrade],
) -> dict[str, Any]:
    """Attribute one decision bar using ledger indices only (no decision_iso join)."""
    exec_trade = by_entry.get(bar_index)
    exit_trade = by_exit.get(bar_index)
    actives = list(active.get(bar_index) or [])

    if exec_trade is not None:
        # COMBO_02: signal and execution occur on the same setup bar.
        same_bar_signal = True
        return {
            "trade": exec_trade.trade,
            "trade_id": exec_trade.trade_id,
            "signal_time": exec_trade.signal_time.isoformat()
            if exec_trade.signal_time
            else None,
            "entry_time": exec_trade.entry_time.isoformat()
            if exec_trade.entry_time
            else None,
            "exit_time": exec_trade.exit_time.isoformat()
            if exec_trade.exit_time
            else None,
            "signal_bar": True,
            "execution_bar": True,
            "entry": "YES",
            "entry_attribution_type": "EXECUTION_BAR",
            "row_role": "EXECUTION_BAR",
            "accepted_signal": True,
            "same_bar_signal_and_execution": same_bar_signal,
        }

    # Active open position (not the execution bar). Prefer non-entry actives.
    position_actives = [lt for lt in actives if lt.entry_index != bar_index]
    if position_actives:
        lt = position_actives[0]
        is_exit = exit_trade is not None and exit_trade.trade_id == lt.trade_id
        return {
            "trade": lt.trade,
            "trade_id": lt.trade_id,
            "signal_time": lt.signal_time.isoformat() if lt.signal_time else None,
            "entry_time": lt.entry_time.isoformat() if lt.entry_time else None,
            "exit_time": lt.exit_time.isoformat() if lt.exit_time else None,
            "signal_bar": False,
            "execution_bar": False,
            "entry": "NO",
            "entry_attribution_type": "POSITION_ACTIVE",
            "row_role": "TRADE_EXIT_BAR" if is_exit else "POSITION_ACTIVE_BAR",
            "accepted_signal": False,
            "same_bar_signal_and_execution": False,
        }

    return {
        "trade": None,
        "trade_id": None,
        "signal_time": None,
        "entry_time": None,
        "exit_time": None,
        "signal_bar": False,
        "execution_bar": False,
        "entry": "NO",
        "entry_attribution_type": "NO_ENTRY",
        "row_role": "NO_TRADE_BAR",
        "accepted_signal": False,
        "same_bar_signal_and_execution": False,
    }


def classify_15m_status(
    *,
    source_available: bool,
    snap_quality: str | None,
    last_closed_candle_time: str | None,
    decision_time: datetime | None,
    missing_reason: str | None = None,
) -> dict[str, Any]:
    """Separate 15m source availability from feature completeness."""
    if not source_available:
        return {
            "15m_source_available": False,
            "15m_feature_available": False,
            "15m_status": "UNAVAILABLE",
            "15m_last_closed_candle_time": None,
            "15m_missing_reason": missing_reason or "15M_SOURCE_UNAVAILABLE",
        }

    q = str(snap_quality or "").upper()
    if q in {"UNKNOWN_DATA_MISSING", "UNKNOWN"} or not last_closed_candle_time:
        return {
            "15m_source_available": True,
            "15m_feature_available": False,
            "15m_status": "UNKNOWN_DATA_MISSING",
            "15m_last_closed_candle_time": last_closed_candle_time,
            "15m_missing_reason": missing_reason or "15M_NO_CLOSED_OR_MISSING_FEATURES",
        }
    if q == "UNKNOWN_INSUFFICIENT_HISTORY":
        return {
            "15m_source_available": True,
            "15m_feature_available": False,
            "15m_status": "INSUFFICIENT_HISTORY",
            "15m_last_closed_candle_time": last_closed_candle_time,
            "15m_missing_reason": missing_reason or "15M_INSUFFICIENT_HISTORY",
        }

    feat = parse_ts(last_closed_candle_time)
    if decision_time is not None and feat is not None and feat > decision_time:
        return {
            "15m_source_available": True,
            "15m_feature_available": False,
            "15m_status": "TIMESTAMP_MISMATCH",
            "15m_last_closed_candle_time": last_closed_candle_time,
            "15m_missing_reason": "15M_FEATURE_AFTER_DECISION",
        }

    # Feature available when quality OK and labels are not UNKNOWN_DATA_MISSING.
    return {
        "15m_source_available": True,
        "15m_feature_available": True,
        "15m_status": "OK",
        "15m_last_closed_candle_time": last_closed_candle_time,
        "15m_missing_reason": None,
    }
