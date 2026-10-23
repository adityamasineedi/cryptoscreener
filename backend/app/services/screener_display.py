"""Display-layer reads of the setup payload (owner D199 / D230): every screen shows the screener view.

The screener view (``payload["screener_setup"]``, the episode-impulse engine) is what the screener table,
the symbol detail Setup tab, ``/api/signals`` and Trade chances show. Paper decides on the payload itself;
nothing here is read by a decision path, and ``paper_trade.py`` is not changed: Trade chances is rebuilt
here from the same per-row function (``_opportunity_from_payload``) on the display payload.

D204 (display only): a trade plan whose stop is on the wrong side of the price the chart labels
BUY/SELL is not shown. The screen says ``invalid plan: stop on wrong side``. Shown prices use the
same half-up tick rounding as ``app.alerts.plan.round_to_tick``, and the wrong-side test is that
file's ``INVALID_STOP`` rule, ``(entry - stop) * sgn <= 0``. The app does not import the alert
package (the sidecar stays a reader). Ticks come from the sidecar's ``tick_sizes.json``, then from
an exchange tick already on the market store. A symbol with neither is left unrounded (the
sidecar's NO_TICK_SIZE: never guess a tick). Chart annotations are not an alert source.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Mapping

from app.services.screener_pullback import display_payload as _overlay_screener_view

__all__ = [
    "INVALID_PLAN_TEXT",
    "chart_annotations",
    "display_payload",
    "list_display_opportunities",
]

# Exact chart / Setup copy (owner D204). Not an alert.
INVALID_PLAN_TEXT = "invalid plan: stop on wrong side"
# Same code app.alerts.plan returns when (close - stop) * sgn <= 0.
INVALID_STOP = "INVALID_STOP"

_ENTRY_KINDS = frozenset({"BUY", "SELL", "ENTRY"})
_STOP_KINDS = frozenset({"SL", "STOP"})
_TICK_FILE = Path(__file__).resolve().parents[1] / "alerts" / "tick_sizes.json"
_alert_ticks: dict[str, float] | None = None


def round_to_tick(price: float, tick: float) -> float:
    """Half-up tick rounding. Byte-for-byte the rule in ``app.alerts.plan.round_to_tick``."""
    t = Decimal(str(tick))
    steps = (Decimal(str(price)) / t).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(steps * t)


def _tick_decimals(tick: float) -> int:
    exp = Decimal(str(tick)).normalize().as_tuple().exponent
    return max(0, -int(exp))


def fmt_price(price: float, tick: float) -> str:
    """Same text as ``app.alerts.plan.fmt_price``."""
    return f"{round_to_tick(price, tick):.{_tick_decimals(tick)}f}"

def _kind(ann: Mapping[str, Any]) -> str:
    return str(ann.get("kind") or "").upper()


def _alert_tick(symbol: str) -> float | None:
    """Tick from the sidecar's tick_sizes.json. Missing symbols are not guessed."""
    global _alert_ticks
    if _alert_ticks is None:
        raw = json.loads(_TICK_FILE.read_text(encoding="utf-8"))["ticks"]
        _alert_ticks = {str(k).upper(): float(v) for k, v in raw.items() if v and float(v) > 0}
    tick = _alert_ticks.get(str(symbol or "").upper())
    return float(tick) if tick else None


def _exchange_tick(symbol: str) -> float | None:
    """Exchange tick already on the market store. No generic fallback."""
    sym = str(symbol or "").upper()
    if not sym:
        return None
    try:
        from app.services.market_store import market_store

        info = market_store.symbols.get(sym)
        raw = getattr(info, "tick_size", None) if info is not None else None
        if raw is not None and float(raw) > 0:
            return float(raw)
    except Exception:  # noqa: BLE001 - display falls through to "no tick"
        return None
    return None


def tick_for_display(symbol: str | None, override: float | None = None) -> float | None:
    if override is not None and float(override) > 0:
        return float(override)
    sym = str(symbol or "").upper()
    if not sym:
        return None
    return _alert_tick(sym) or _exchange_tick(sym)


def _side(entry_ann: Mapping[str, Any] | None, payload: Mapping[str, Any]) -> str | None:
    """The side the chart labels, then the payload direction, then the BOS direction."""
    if entry_ann is not None:
        kind = _kind(entry_ann)
        if kind == "SELL":
            return "SHORT"
        if kind == "BUY":
            return "LONG"
        direction = str(entry_ann.get("direction") or "").upper()
        if direction in ("SHORT", "SELL"):
            return "SHORT"
        if direction in ("LONG", "BUY"):
            return "LONG"
    direction = str(payload.get("direction") or "").upper()
    if direction in ("SHORT", "SELL"):
        return "SHORT"
    if direction in ("LONG", "BUY"):
        return "LONG"
    entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else {}
    direction = str((entry or {}).get("direction") or "").upper()
    if direction in ("SHORT", "SELL"):
        return "SHORT"
    if direction in ("LONG", "BUY"):
        return "LONG"
    bos = payload.get("bos") if isinstance(payload.get("bos"), Mapping) else {}
    bos_dir = str((bos or {}).get("direction") or "").upper()
    if "BEARISH" in bos_dir:
        return "SHORT"
    if "BULLISH" in bos_dir:
        return "LONG"
    return None


def _would_add_entry(payload: Mapping[str, Any], anns: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The BUY/SELL line ``ensure_trade_plan_entry_annotation`` would add. Read-only."""
    if any(_kind(a) in _ENTRY_KINDS for a in anns):
        return None
    if not any(str(a.get("group") or "") == "trade_plan" for a in anns):
        return None
    from app.signals.signal_engine import trade_plan_entry_annotation

    entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else None
    stop = payload.get("stop") if isinstance(payload.get("stop"), Mapping) else None
    bos = payload.get("bos") if isinstance(payload.get("bos"), Mapping) else None
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    plan_time = next((a.get("time") for a in anns if a.get("group") == "trade_plan" and a.get("time")), None)
    return trade_plan_entry_annotation(
        entry=dict(entry) if entry else None,
        stop=dict(stop) if stop else None,
        bos=dict(bos) if bos else None,
        targets=targets,
        plan_time=str(plan_time) if plan_time else None,
        direction=payload.get("direction") if isinstance(payload.get("direction"), str) else None,
    )


def _plan_levels(
    payload: Mapping[str, Any], anns: list[Mapping[str, Any]]
) -> tuple[float | None, float | None, str | None]:
    """Entry and stop as the chart overlay shows them, and the labelled side."""
    entry_ann = next((a for a in anns if _kind(a) in _ENTRY_KINDS and a.get("price") is not None), None)
    if entry_ann is None:
        entry_ann = _would_add_entry(payload, anns)
    stop_ann = next((a for a in anns if _kind(a) in _STOP_KINDS and a.get("price") is not None), None)
    entry_px = entry_ann.get("price") if entry_ann else None
    stop_px = stop_ann.get("price") if stop_ann else None
    entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else {}
    stop = payload.get("stop") if isinstance(payload.get("stop"), Mapping) else {}
    if entry_px is None:
        if entry.get("entry_price") is not None:
            entry_px = entry.get("entry_price")
        elif stop.get("entry_price") is not None:
            entry_px = stop.get("entry_price")
    if stop_px is None:
        stop_px = stop.get("final_stop")
    try:
        entry_f = float(entry_px) if entry_px is not None else None
        stop_f = float(stop_px) if stop_px is not None else None
    except (TypeError, ValueError):
        return None, None, None
    return entry_f, stop_f, _side(entry_ann, payload)


def stop_side_code(entry: float, stop: float, side: str) -> str | None:
    """``plan.build_plan`` INVALID_STOP: ``dist = (close - stop) * sgn <= 0``.

    The chart's close is the labelled BUY/SELL price.
    """
    sgn = 1.0 if side == "LONG" else -1.0
    if (float(entry) - float(stop)) * sgn <= 0:
        return INVALID_STOP
    return None


def _off_tick(price: Any, tick: float) -> bool:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return False
    return abs(value - round_to_tick(value, tick)) > tick * 1e-9


def _needs_rounding(payload: Mapping[str, Any], anns: list[Mapping[str, Any]], tick: float | None) -> bool:
    if tick is None:
        return False
    for ann in anns:
        if str(ann.get("group") or "") == "trade_plan" and ann.get("price") is not None and _off_tick(ann.get("price"), tick):
            return True
    entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else {}
    stop = payload.get("stop") if isinstance(payload.get("stop"), Mapping) else {}
    if entry.get("entry_price") is not None and _off_tick(entry.get("entry_price"), tick):
        return True
    if stop.get("final_stop") is not None and _off_tick(stop.get("final_stop"), tick):
        return True
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    return any(isinstance(t, Mapping) and t.get("target_price") is not None and _off_tick(t.get("target_price"), tick) for t in targets)


def _round_ann(ann: Mapping[str, Any], tick: float) -> dict[str, Any]:
    out = dict(ann)
    if str(out.get("group") or "") == "trade_plan" and out.get("price") is not None:
        try:
            rounded = round_to_tick(float(out["price"]), tick)
        except (TypeError, ValueError):
            return out
        out["price"] = rounded
        out["price_text"] = fmt_price(rounded, tick)
    return out


def _present(shown: dict[str, Any], *, symbol: str | None, tick: float | None) -> dict[str, Any]:
    """Copy ``shown`` only when the displayed plan changes. Never mutates ``shown``."""
    anns = [a for a in (shown.get("annotations") or []) if isinstance(a, Mapping)]
    entry_px, stop_px, side = _plan_levels(shown, anns)
    invalid = (
        entry_px is not None
        and stop_px is not None
        and side in ("LONG", "SHORT")
        and stop_side_code(entry_px, stop_px, side) == INVALID_STOP
    )
    use_tick = tick_for_display(symbol, tick)
    if not invalid and not _needs_rounding(shown, anns, use_tick):
        return shown
    out = dict(shown)
    if invalid:
        out["plan_notice"] = INVALID_PLAN_TEXT
        out["annotations"] = [dict(a) for a in anns if str(a.get("group") or "") != "trade_plan"]
        stop = shown.get("stop")
        if isinstance(stop, Mapping):
            out["stop"] = {**stop, "final_stop": None}
        entry = shown.get("entry")
        if isinstance(entry, Mapping) and entry.get("entry_price") is not None:
            out["entry"] = {**entry, "entry_price": None}
        if isinstance(shown.get("targets"), list) and shown.get("targets"):
            out["targets"] = []
        return out
    assert use_tick is not None
    out["annotations"] = [_round_ann(a, use_tick) for a in anns]
    entry = shown.get("entry")
    if isinstance(entry, Mapping) and entry.get("entry_price") is not None:
        out["entry"] = {**entry, "entry_price": round_to_tick(float(entry["entry_price"]), use_tick)}
    stop = shown.get("stop")
    if isinstance(stop, Mapping) and stop.get("final_stop") is not None:
        out["stop"] = {**stop, "final_stop": round_to_tick(float(stop["final_stop"]), use_tick)}
    targets = shown.get("targets")
    if isinstance(targets, list):
        rounded_targets = []
        for t in targets:
            if isinstance(t, Mapping) and t.get("target_price") is not None:
                rounded_targets.append({**t, "target_price": round_to_tick(float(t["target_price"]), use_tick)})
            elif isinstance(t, Mapping):
                rounded_targets.append(dict(t))
            else:
                rounded_targets.append(t)
        out["targets"] = rounded_targets
    return out


def display_payload(payload: Any, *, symbol: str | None = None, tick: float | None = None) -> Any:
    """Screener view, then the D204 display rules. The stored payload is not modified."""
    shown = _overlay_screener_view(payload)
    if not isinstance(shown, dict):
        return shown
    sym = symbol if symbol is not None else shown.get("symbol")
    return _present(shown, symbol=str(sym) if sym else None, tick=tick)


def chart_annotations(
    payload: Any, *, symbol: str
) -> tuple[list[dict[str, Any]], str | None, Any]:
    """Annotations for the chart overlay, after the entry line is filled in.

    A wrong-side plan is removed here even if that line is what makes the stop wrong-sided.
    The third value is the display status (the screener view), not the status paper reads.
    """
    from app.signals.signal_engine import ensure_trade_plan_entry_annotation

    shown = display_payload(payload, symbol=symbol)
    if not isinstance(shown, dict):
        return [], None, None
    anns = ensure_trade_plan_entry_annotation(shown)
    notice = shown.get("plan_notice")
    status = shown.get("status")
    if notice:
        kept = [a for a in anns if str(a.get("group") or "") != "trade_plan"]
        return kept, str(notice), status
    checked = _present({**shown, "annotations": anns}, symbol=symbol, tick=None)
    if checked.get("plan_notice"):
        return list(checked.get("annotations") or []), str(checked["plan_notice"]), status
    return list(checked.get("annotations") or []), None, status


# paper_trade.list_trade_opportunities tier order (kept in step; tested against it).
_TIER_RANK = {"READY": 0, "NEAR": 1, "FORMING": 2, "WATCH": 3, "WAITING": 4, "BLOCKED": 5}


def list_display_opportunities(
    *,
    limit: int = 40,
    open_symbols: set[str] | None = None,
    include_waiting: bool = True,
) -> list[dict[str, Any]]:
    """``paper_trade.list_trade_opportunities`` on the display payload.

    Same rows, tiers, ordering and limit when no screener view differs. Where the screener view makes a
    row READY but the payload paper reads does not, the row says so: paper does not open it.
    """
    from app.services import paper_trade as pt
    from app.services.engine_store import engine_store

    open_set = {s.upper() for s in (open_symbols or set())}
    mode = pt.PATH_A
    legacy_opens = False
    try:
        eng = pt.get_paper_trade_engine()
        mode = eng.entry_mode
        legacy_opens = (
            bool(eng.enabled)
            and bool(getattr(eng, "legacy_auto_entry_enabled", False))
            and not (bool(getattr(eng, "v1_watcher_owns_entries", False)) and mode != pt.PATH_B)
        )
    except Exception:  # noqa: BLE001
        mode = pt.PATH_A
    rows: list[dict[str, Any]] = []
    for sym, payload in list(engine_store.setup_signals.items()):
        if not isinstance(payload, dict):
            continue
        if not str(sym or "").upper().isalnum() or not str(sym or "").isascii():
            continue
        kw = dict(already_open=sym.upper() in open_set, include_waiting=include_waiting,
                  entry_mode=mode, legacy_opens=legacy_opens)
        shown = display_payload(payload, symbol=sym)
        row = pt._opportunity_from_payload(sym, shown, **kw)  # noqa: SLF001 - display reuse, no edit
        if row is None:
            continue
        if shown is not payload:
            paper_row = pt._opportunity_from_payload(sym, payload, **kw)  # noqa: SLF001
            row["setup_view"] = "screener"
            row["paper_tier"] = paper_row.get("tier") if paper_row else None
            row["paper_status"] = payload.get("status")
            if row.get("tier") == "READY" and row["paper_tier"] != "READY":
                row["chance"] = (
                    "Screener entry candidate (episode impulse) - display only; "
                    "paper reads the shared engine and does not open it"
                )
        rows.append(row)
    rows.sort(
        key=lambda r: (
            _TIER_RANK.get(str(r.get("tier")), 9),
            -int(r.get("pass_count") or 0),
            -float(r.get("score") or 0),
            str(r.get("symbol") or ""),
        )
    )
    return rows[: max(1, min(int(limit), 300))]
