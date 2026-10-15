"""Ranked strategy board for the Strategies UI.

Read-only view of ``backend/scripts/strategy_rank/ranked.json`` (written by
``merge_rank.py``). Keeps the file's order, pass flags and sums as they are.
Does not run a backtest, write the database, or touch entry/exit logic.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RANK_FILE = Path(__file__).resolve().parents[2] / "scripts" / "strategy_rank" / "ranked.json"
RANK_FILE_LABEL = "backend/scripts/strategy_rank/ranked.json"

# Report order (REPORT.md): day, scalp, swing, grid.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("day", "Day trading", "1h"),
    ("scalp", "Scalp", "5m · 15m"),
    ("swing", "Swing", "4h · 1d"),
    ("grid", "Grid", "grid engines"),
)
# Coin order used in REPORT.md. Symbols outside it sort after, by name.
REPORT_COIN_ORDER = ("BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK")
# REPORT.md: the two ids share most trade lists. ranked.json still lists both.
NEAR_ALIASES = {"COMBO_02_LOCAL": "COMBO_01"}
# REPORT.md: grid range research engine. Never shown as a pass.
GRID_RESEARCH_PREFIX = "GRID-"

BOARD_CAVEATS: tuple[str, ...] = (
    "Nothing here is production-ready on this evidence.",
    "In-sample only: one window, no out-of-sample test.",
    "COMBO_01 and COMBO_02_LOCAL are nearly the same strategy. COMBO_02_CLOSED_HTF "
    "matches COMBO_02. They are aliases, not separate winners.",
    "COMBO strategies fill at the signal close with no slippage. CANDLE12_V2 and the "
    "OSE variants include 0.02% slippage, so their figures are the more conservative ones.",
)
GROUP_CAVEATS: dict[str, tuple[str, ...]] = {
    "day": ("Shorts on 1h are negative or marginal across the ten coins.",),
    "scalp": (
        "Only 3 cells pass, and those strategies lose across the ten coins as a whole.",
        "Intraday BOS/SD is sized at 2.50 USD risk and 1x, so its USD figures are about "
        "8 times smaller in scale than the others.",
    ),
    "swing": ("0 cells pass because no cell reaches 15 trades.",),
    "grid": (
        "Combined net is the sum of ten separate 1000 USD accounts.",
        "Funding is not included in any grid net figure.",
        "The grid range research engine (GRID-A, GRID-B, GRID-C, GRID-A-LVC) is not a "
        "pass. Do not use it.",
    ),
}

_cache: dict[str, Any] = {"stamp": None, "data": None}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _load_rank_file(path: Path) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (data, status, error). Status is OK, MISSING, EMPTY or INVALID."""
    try:
        stat = path.stat()
    except OSError:
        return None, "MISSING", f"{RANK_FILE_LABEL} not found. Run merge_rank.py to write it."
    if stat.st_size == 0:
        return None, "EMPTY", f"{RANK_FILE_LABEL} is empty."
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache["stamp"] == stamp and _cache["data"] is not None:
        return _cache["data"], "OK", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "INVALID", f"{RANK_FILE_LABEL} could not be read: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("strategy_rollup"), dict):
        return None, "INVALID", f"{RANK_FILE_LABEL} has no strategy_rollup."
    if not any(data["strategy_rollup"].get(g[0]) for g in GROUPS):  # nothing ranked at all
        return None, "EMPTY", f"{RANK_FILE_LABEL} has no ranked strategies."
    _cache["stamp"], _cache["data"] = stamp, data
    return data, "OK", None


def fill_label(execution_model: str) -> str:
    """Short fill-model label from the rank row's execution_model text."""
    text = str(execution_model or "").strip()
    if text == "LEGACY_SIGNAL_CLOSE":
        return "Signal close, no slippage"
    if text == "CAUSAL_NEXT_OPEN":
        return "Next bar open"
    hit = re.search(r"(\d+(?:\.\d+)?)% adverse", text)
    if hit:
        return f"{hit.group(1)}% slippage included"
    if "no slippage" in text.lower():
        if "signal-bar close" in text.lower():
            return "Signal close, no slippage"
        return "No slippage"
    return text[:60] or "Not stated"


def _plain_name(strategy_id: str) -> str:
    """Readable name for an id the catalog does not carry."""
    # REPORT.md: GEO is geometric spacing, ARI is arithmetic; the number is the grid count.
    hit = re.fullmatch(r"GTB-(GEO|ARI)-(\d+)", strategy_id)
    if hit:
        spacing = "geometric" if hit.group(1) == "GEO" else "arithmetic"
        return f"Grid trading book, {spacing} spacing, {hit.group(2)} grids"
    return strategy_id.replace("_", " ").replace("-", " ")


def _direction_fits(catalog_direction: Any, direction: str) -> bool:
    return direction.upper() in str(catalog_direction or "").upper()


def _describe(
    strategy_id: str,
    direction: str,
    family: str,
    engine: str,
    strategies: list[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """What the strategy is, from the catalog when the id matches. No rules are made up."""
    base, _, variant = strategy_id.partition(".")
    match = next((s for s in strategies if s.get("id") == strategy_id), None)
    if match is None:
        match = next(
            (
                s
                for s in strategies
                if s.get("combo_id") == strategy_id
                and _direction_fits(s.get("direction"), direction)
            ),
            None,
        )
    if match is None and variant:
        # "INTRADAY_BOS_SD_V1.A" is variant A of the catalog's INTRADAY_BOS_SD_V1.
        match = next((s for s in strategies if s.get("id") == base), None)
    else:
        variant = ""
    if match is not None:
        return {
            "in_catalog": True,
            "catalog_id": match.get("id"),
            "name": match.get("name") or strategy_id,
            "description_source": "catalog",
            "summary": match.get("summary") or "",
            "gates": list(match.get("gates") or []),
            "variant": variant or None,
        }
    combo = registry.get(strategy_id)
    if combo is not None:
        return {
            "in_catalog": False,
            "catalog_id": None,
            "name": str(combo.get("name") or strategy_id).replace("_", " "),
            "description_source": "combination_registry",
            "summary": combo.get("description") or "",
            "gates": list(combo.get("conditions") or []),
            "variant": None,
        }
    return {
        "in_catalog": False,
        "catalog_id": None,
        "name": _plain_name(strategy_id),
        "description_source": "rank_file",
        "summary": (
            f"Not in the strategy catalog. Family: {family or 'unknown'}"
            + (f" (engine: {engine})." if engine else ".")
        ),
        "gates": [],
        "variant": None,
    }


def _cell_view(cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(cell.get("symbol") or ""),
        "coin": str(cell.get("symbol") or "").replace("USDT", ""),
        "net_pnl_usd": _num(cell.get("net_pnl_usd")),
        "win_rate": _num(cell.get("win_rate")),
        "profit_factor": _num(cell.get("profit_factor")),
        "trades_closed": int(_num(cell.get("trades_closed")) or 0),
        "fees_usd": _num(cell.get("fees_usd")),
        "max_drawdown_usd": _num(cell.get("max_drawdown_usd")),
        "passes": bool(cell.get("passes")),
        "fail_reasons": list(cell.get("fail_reasons") or []),
    }


def _coin_sort_key(coin: str) -> tuple[int, str]:
    try:
        return (REPORT_COIN_ORDER.index(coin), coin)
    except ValueError:
        return (len(REPORT_COIN_ORDER), coin)


def _near_alias_counts(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """For each near-alias id: cells it shares with its twin, and how many are identical."""
    idx = {
        (c.get("strategy_id"), c.get("direction"), c.get("symbol"), c.get("timeframe")): c
        for c in cells
    }
    out: dict[str, dict[str, int]] = {}
    for alias, twin in NEAR_ALIASES.items():
        shared = same = 0
        for key, cell in idx.items():
            if key[0] != alias:
                continue
            other = idx.get((twin,) + key[1:])
            if other is None:
                continue
            shared += 1
            if cell.get("trades_closed") == other.get("trades_closed") and abs(
                (_num(cell.get("net_pnl_usd")) or 0.0) - (_num(other.get("net_pnl_usd")) or 0.0)
            ) < 1e-9:
                same += 1
        out[alias] = {"cells": shared, "cells_identical": same}
    return out


def _most_common(values: list[Any]) -> Any:
    counted = Counter(v for v in values if v is not None)
    return counted.most_common(1)[0][0] if counted else None


def build_strategy_rank_board(
    catalog: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Board payload for GET /research/strategies/rank. Never raises on a bad file."""
    rank_path = path or RANK_FILE
    data, status, error = _load_rank_file(rank_path)
    base: dict[str, Any] = {
        "status": status,
        "error": error,
        "source": {"path": RANK_FILE_LABEL, "written_by": "backend/scripts/strategy_rank/merge_rank.py"},
        "read_only": True,
        "groups": [],
    }
    if data is None:
        return base

    if catalog is None:
        from app.research.strategy_catalog import build_strategy_catalog

        catalog = build_strategy_catalog()
    strategies = [s for s in (catalog.get("strategies") or []) if isinstance(s, dict)]
    registry = {
        str(c.get("combination_id")): c
        for c in (catalog.get("all_combinations") or [])
        if isinstance(c, dict)
    }

    rollup = data.get("strategy_rollup") or {}
    cells_ranked = data.get("cells_ranked") or {}
    summary = data.get("summary") or {}
    all_cells = [c for g in GROUPS for c in (cells_ranked.get(g[0]) or []) if isinstance(c, dict)]
    study_cells = [c for c in all_cells if c.get("style") != "grid"]

    default_risk = _most_common([_num(c.get("risk_usd")) for c in study_cells])
    default_leverage = _most_common([_num(c.get("leverage")) for c in study_cells])
    near = _near_alias_counts(all_cells)
    dropped = {
        alias: info
        for alias, info in (summary.get("alias_ids") or {}).items()
        if isinstance(info, dict) and info.get("dropped")
    }

    groups: list[dict[str, Any]] = []
    for style, label, timeframes in GROUPS:
        by_row: dict[tuple, list[dict[str, Any]]] = {}
        for cell in cells_ranked.get(style) or []:
            key = (cell.get("family"), cell.get("strategy_id"), cell.get("direction"), cell.get("timeframe"))
            by_row.setdefault(key, []).append(cell)

        rows: list[dict[str, Any]] = []
        hidden = {"duplicates": 0, "failures": 0}
        # Order is strategy_rollup's own: most coins passing, then higher combined net.
        for roll in rollup.get(style) or []:
            sid = str(roll.get("strategy_id") or "")
            direction = str(roll.get("direction") or "").upper()
            family = str(roll.get("family") or "")
            cells = by_row.get((roll.get("family"), roll.get("strategy_id"), roll.get("direction"), roll.get("timeframe")), [])
            engine = str(cells[0].get("engine") or "") if cells else ""
            models = [str(m) for m in (roll.get("execution_models") or [])]
            leverage = sorted({v for v in (_num(c.get("leverage")) for c in cells) if v is not None})
            risk = [v for v in (_num(r) for r in (roll.get("risk_usd") or [])) if v is not None]

            sizing_note = None
            if style == "grid":
                sizing_note = f"{leverage[0]:g}x, no per-trade risk" if leverage else "no per-trade risk"
            elif (risk and risk != [default_risk]) or (leverage and leverage != [default_leverage]):
                parts = [f"{risk[0]:.2f} USD risk"] if risk else []
                parts += [f"{leverage[0]:g}x"] if leverage else []
                sizing_note = ", ".join(parts)

            aliases: list[dict[str, Any]] = []
            for alias, info in dropped.items():
                if info.get("same_as") == sid:
                    aliases.append({
                        "id": alias,
                        "kind": "identical",
                        "note": f"identical in {info.get('cells_identical')} of {info.get('cells')} cells; "
                                "left out of the ranking",
                    })
            alias_of = None
            for alias, twin in NEAR_ALIASES.items():
                counts = near.get(alias) or {}
                note = (
                    f"same trade count and net in {counts.get('cells_identical', 0)} of "
                    f"{counts.get('cells', 0)} shared cells"
                )
                if sid == twin:
                    aliases.append({"id": alias, "kind": "near", "note": note})
                elif sid == alias:
                    alias_of = {"id": twin, "kind": "near", "note": note}

            is_grid_research = style == "grid" and sid.startswith(GRID_RESEARCH_PREFIX)
            # Left off the board: a second name for the same strategy, and anything
            # that lost money, made no trades, or is the grid research engine.
            if alias_of is not None:
                hidden["duplicates"] += 1
                continue
            net = _num(roll.get("net_pnl_usd"))
            if is_grid_research or net is None or net <= 0 or not int(_num(roll.get("trades")) or 0):
                hidden["failures"] += 1
                continue
            rows.append({
                "rank": len(rows) + 1,
                "key": f"{style}|{sid}|{direction}|{roll.get('timeframe')}",
                "style": style,
                "family": family,
                "strategy_id": sid,
                "direction": direction,
                "timeframe": roll.get("timeframe"),
                **_describe(sid, direction, family, engine, strategies, registry),
                "coins": int(_num(roll.get("coins")) or 0),
                "coins_passing": int(_num(roll.get("coins_passing")) or 0),
                "coins_net_positive": int(_num(roll.get("coins_net_positive")) or 0),
                "passing_symbols": list(roll.get("passing_symbols") or []),
                "trades": int(_num(roll.get("trades")) or 0),
                "net_pnl_usd": _num(roll.get("net_pnl_usd")),
                "fees_usd": _num(roll.get("fees_usd")),
                "worst_cell_net_usd": _num(roll.get("worst_cell_net_usd")),
                "best_cell_net_usd": _num(roll.get("best_cell_net_usd")),
                "execution_models": models,
                "fill_label": " / ".join(dict.fromkeys(fill_label(m) for m in models)) or "Not stated",
                "risk_usd": risk,
                "leverage": leverage,
                "sizing_note": sizing_note,
                "usd_comparable": bool(roll.get("usd_comparable")),
                "aliases": aliases,
                "alias_of": alias_of,
                "do_not_use": is_grid_research,
                "do_not_use_reason": (
                    "Grid range research engine. Not a pass. Do not use it."
                    if is_grid_research
                    else None
                ),
                "cells": sorted(
                    (_cell_view(c) for c in cells),
                    key=lambda c: _coin_sort_key(c["coin"]),
                ),
            })
        per_style = (summary.get("per_style") or {}).get(style) or {}
        groups.append({
            "id": style,
            "label": label,
            "timeframes": timeframes,
            "cells": per_style.get("cells"),
            "cells_net_positive": per_style.get("cells_net_positive"),
            "cells_passing": per_style.get("cells_passing"),
            "caveats": list(GROUP_CAVEATS.get(style, ())),
            "hidden": hidden,
            "rows": rows,
        })

    starts = sorted(str(c.get("window_start_utc"))[:10] for c in all_cells if c.get("window_start_utc"))
    ends = sorted(str(c.get("window_end_utc"))[:10] for c in all_cells if c.get("window_end_utc"))
    coins = sorted({str(c.get("symbol") or "").replace("USDT", "") for c in all_cells} - {""}, key=_coin_sort_key)
    try:
        modified = datetime.fromtimestamp(rank_path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        modified = None

    return {
        **base,
        "source": {**base["source"], "modified_utc": modified},
        "window": {
            "start": starts[0] if starts else None,
            "end": ends[-1] if ends else None,
            "note": "1d candles end 2026-10-08",
            "in_sample": True,
            "sample_note": "In-sample, one window, no out-of-sample test.",
            "coins": coins,
            "market": "USDT perps",
            "account": {
                "principal_usd": _most_common([_num(c.get("principal_usd")) for c in study_cells]),
                "risk_usd": default_risk,
                "leverage": default_leverage,
                "exceptions": "Intraday BOS/SD is 2.50 USD risk and 1x. Grid is 1x.",
            },
            "fees": {
                "taker_pct": _most_common([_num(c.get("taker_fee_pct")) for c in all_cells]),
                "maker_pct": _most_common([_num(c.get("maker_fee_pct")) for c in all_cells]),
            },
        },
        "pass_rule": {
            "text": "A coin passes only when all four hold: net profit above 0, profit factor above "
                    "1.2, at least 15 closed trades, and fees smaller than gross profit.",
            "rule": summary.get("rule") or {},
        },
        "caveats": list(BOARD_CAVEATS),
        "cells_ok": summary.get("cells_ok"),
        "cells_not_run": summary.get("cells_not_ok"),
        "groups": groups,
    }
