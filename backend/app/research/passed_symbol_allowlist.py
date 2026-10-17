"""Per-coin allowlist for the passed book.

Each PASSED_SIDES row is tested on each coin inside the regime book. A coin
with an entry in ``passed_symbol_allowlist.json`` trades only the sides
listed there; the passed sides missing from the list failed that coin's test
and are not traded on it. A coin with no entry trades every passed side, as
before. Both the paper watcher and the backtest regime book read this file,
so the two take and skip the same signals.

File shape (version 1)::

    {"version": 1,
     "symbols": {
       "BTCUSDT": {
         "allowed_sides": [{"strategy_id": "COMBO_01", "direction": "SHORT", "timeframe": "1h"}],
         "tested": {"window": {"start": "...", "end": "..."},
                    "pass_rule": "net > 0 and trades >= 5",
                    "pnl_model": "CAUSAL_NEXT_OPEN",
                    "decided_at": "...",
                    "results": [ per-side rows ]}}}}

Every allowed side must be a row of PASSED_SIDES; anything else is a load
error (``AllowlistError``). Sides are tuples in PASSED_SIDES order:
``(strategy_id, timeframe, direction)``.

This only filters the book. It does not change signals, regime gating,
fills, fees, sizing, or the one-position rule.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.research.passed_cap_book import PASSED_SIDES, cap_symbols

ALLOWLIST_PATH = Path(__file__).with_name("passed_symbol_allowlist.json")
ALLOWLIST_VERSION = 1
DEFAULT_PASS_RULE = "net > 0 and trades >= 5"

# Skip reasons. Paper journal (lower case) and backtest skipped_signals (upper).
PAPER_SKIP_REASON = "symbol_side_not_allowed"
BACKTEST_SKIP_REASON = "SYMBOL_SIDE_NOT_ALLOWED"

Side = tuple[str, str, str]  # (strategy_id, timeframe, direction), as PASSED_SIDES

_DIRECTIONS = ("LONG", "SHORT")


class AllowlistError(ValueError):
    """The allowlist file is missing, malformed, or names a side outside PASSED_SIDES."""


def _side_row(side: Side) -> dict[str, str]:
    strategy_id, timeframe, direction = side
    return {"strategy_id": strategy_id, "timeframe": timeframe, "direction": direction}


@dataclass(frozen=True)
class SymbolEntry:
    """One coin's tested allowlist."""

    symbol: str
    allowed: tuple[Side, ...]  # PASSED_SIDES order
    tested: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> tuple[Side, ...]:
        """1h PASSED_SIDES rows that failed this coin's test, in book order."""
        allowed = set(self.allowed)
        return tuple(side for side in PASSED_SIDES if side[1] == "1h" and side not in allowed)

    def describe(self, scope: Iterable[Side] | None = None) -> dict[str, Any]:
        """JSON-ready view: allowed / blocked sides plus the test that decided them.

        ``scope`` limits both lists to those sides (e.g. a direction filter).
        """
        keep = set(scope) if scope is not None else None
        window = dict(self.tested.get("window") or {})
        return {
            "symbol": self.symbol,
            "allowed_sides": [_side_row(s) for s in self.allowed if keep is None or s in keep],
            "blocked_sides": [_side_row(s) for s in self.blocked if keep is None or s in keep],
            "tested_window": {"start": window.get("start"), "end": window.get("end")},
            "decided_at": self.tested.get("decided_at"),
            "pass_rule": self.tested.get("pass_rule"),
            "pnl_model": self.tested.get("pnl_model"),
            "results": [dict(row) for row in (self.tested.get("results") or [])],
        }


@dataclass(frozen=True)
class SymbolAllowlist:
    """Validated allowlist. Plain dict/tuples, so it pickles to the regime worker."""

    entries: dict[str, SymbolEntry]
    version: int = ALLOWLIST_VERSION
    source: str = ""

    def allowed_sides_for(self, symbol: str) -> frozenset[Side] | None:
        """Sides that may trade on ``symbol``; None when the coin has no entry (all allowed)."""
        entry = self.entries.get(str(symbol or "").strip().upper())
        return None if entry is None else frozenset(entry.allowed)

    def is_allowed(self, symbol: str, side: Side) -> bool:
        allowed = self.allowed_sides_for(symbol)
        return allowed is None or tuple(side) in allowed

    def entry(self, symbol: str) -> SymbolEntry | None:
        return self.entries.get(str(symbol or "").strip().upper())

    def describe(
        self, symbols: Iterable[str] | None = None, scope: Iterable[Side] | None = None
    ) -> dict[str, Any]:
        """JSON-ready view of the entries (only ``symbols`` when given)."""
        wanted = None if symbols is None else {str(s).strip().upper() for s in symbols}
        scope_list = list(scope) if scope is not None else None
        return {
            "version": self.version,
            "source": self.source,
            "symbols": {
                sym: entry.describe(scope_list)
                for sym, entry in sorted(self.entries.items())
                if wanted is None or sym in wanted
            },
        }


def _parse_side(raw: Any, where: str) -> Side:
    if not isinstance(raw, Mapping):
        raise AllowlistError(f"{where}: each allowed side must be an object, got {raw!r}")
    strategy_id = str(raw.get("strategy_id") or "").strip()
    direction = str(raw.get("direction") or "").strip().upper()
    timeframe = str(raw.get("timeframe") or "").strip().lower()
    if direction not in _DIRECTIONS:
        raise AllowlistError(f"{where}: direction must be LONG or SHORT, got {raw.get('direction')!r}")
    side = (strategy_id, timeframe, direction)
    if side not in PASSED_SIDES:
        raise AllowlistError(
            f"{where}: {strategy_id or '?'} {direction} {timeframe or '?'} is not a PASSED_SIDES row"
        )
    return side


def _parse_tested(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AllowlistError(f"{where}: 'tested' must be an object with window and decided_at")
    window = raw.get("window")
    if not isinstance(window, Mapping) or not window.get("start") or not window.get("end"):
        raise AllowlistError(f"{where}: tested.window needs start and end")
    if not str(raw.get("decided_at") or "").strip():
        raise AllowlistError(f"{where}: tested.decided_at is required")
    results = raw.get("results", [])
    if not isinstance(results, list) or not all(isinstance(r, Mapping) for r in results):
        raise AllowlistError(f"{where}: tested.results must be a list of objects")
    return json.loads(json.dumps(raw))  # plain, detached copy


def parse_allowlist(data: Any, *, source: str = "") -> SymbolAllowlist:
    """Validate the decoded file. Raises ``AllowlistError`` on anything off."""
    if not isinstance(data, Mapping):
        raise AllowlistError(f"{source or 'allowlist'}: top level must be an object")
    if data.get("version") != ALLOWLIST_VERSION:
        raise AllowlistError(
            f"{source or 'allowlist'}: version must be {ALLOWLIST_VERSION}, got {data.get('version')!r}"
        )
    symbols = data.get("symbols")
    if not isinstance(symbols, Mapping):
        raise AllowlistError(f"{source or 'allowlist'}: 'symbols' must be an object")
    universe = set(cap_symbols())
    entries: dict[str, SymbolEntry] = {}
    for raw_symbol, raw_entry in symbols.items():
        sym = str(raw_symbol or "").strip().upper()
        where = f"symbols.{raw_symbol}"
        if sym != raw_symbol:
            raise AllowlistError(f"{where}: symbol keys must be upper case, e.g. {sym!r}")
        if sym not in universe:
            raise AllowlistError(f"{where}: not a large/mid cap-book symbol")
        if not isinstance(raw_entry, Mapping):
            raise AllowlistError(f"{where}: entry must be an object")
        raw_sides = raw_entry.get("allowed_sides")
        if not isinstance(raw_sides, list):
            raise AllowlistError(f"{where}: allowed_sides must be a list (empty blocks every side)")
        parsed = [_parse_side(row, f"{where}.allowed_sides[{n}]") for n, row in enumerate(raw_sides)]
        if len(set(parsed)) != len(parsed):
            raise AllowlistError(f"{where}: allowed_sides lists a side twice")
        tested = _parse_tested(raw_entry.get("tested"), where)
        allowed = tuple(side for side in PASSED_SIDES if side in set(parsed))
        entries[sym] = SymbolEntry(symbol=sym, allowed=allowed, tested=tested)
    return SymbolAllowlist(entries=entries, version=ALLOWLIST_VERSION, source=source)


def load_allowlist(path: str | Path | None = None) -> SymbolAllowlist:
    """Read and validate the allowlist file (default: the committed one)."""
    target = Path(path) if path is not None else ALLOWLIST_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AllowlistError(f"{target}: allowlist file not found") from exc
    except (OSError, ValueError) as exc:
        raise AllowlistError(f"{target}: cannot read allowlist ({exc})") from exc
    return parse_allowlist(data, source=target.name)


_cached: SymbolAllowlist | None = None
_cache_lock = threading.Lock()


def get_allowlist() -> SymbolAllowlist:
    """The committed allowlist, loaded and validated once per process."""
    global _cached
    with _cache_lock:
        if _cached is None:
            _cached = load_allowlist()
        return _cached


def set_allowlist_for_tests(allowlist: SymbolAllowlist | None) -> None:
    """Replace the process allowlist (None reloads the file on next use)."""
    global _cached
    with _cache_lock:
        _cached = allowlist


def allowed_sides_for(symbol: str) -> frozenset[Side] | None:
    """Sides of PASSED_SIDES allowed on ``symbol``; None = no entry, every passed side allowed."""
    return get_allowlist().allowed_sides_for(symbol)
