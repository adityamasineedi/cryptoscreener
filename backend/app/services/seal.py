"""The global research seal for every backtest the app runs (owner D202 / "D184").

One place for the seal date. Every Backtest-tab run (single strategy, the passed/auto regime
book, the daily books, 4h book 16, the hedge book, grid, the Profit checker and the BOS /
multi-cap research runs) loads OHLCV only up to ``SEAL_LAST_DAY`` 23:59 UTC:

* ``sealed_scope()`` marks the current task as a sealed backtest. ``SealGuardMiddleware`` opens it
  for every backtest HTTP path (``SEALED_PATH_PREFIXES``); the background jobs open it again
  themselves. Inside it the OHLCV loaders (``app.research.postgres_ohlcv``,
  ``app.research.service._load_research_candles``) never ask for a bar at or after
  ``SEAL_END_EXCLUSIVE``: a DB-tail of N bars counts back from the seal, "Max" ends at the seal,
  warm-up bars come from before it, and any bar that still arrives later is dropped.
* An explicit calendar date after the seal is refused, never clamped: ``check_request_dates``
  raises ``SealedRangeError`` and the routes answer HTTP 400 with ``SEALED_MESSAGE``.
* Outside the scope (paper trading, the live watchers, the forward tests, scripts) nothing changes.

The frozen CANDLE12 V3 OOS-2 files (docs/candle12_v3_oos_contract.lock.json) still spell the
date on their own and are not edited.

Source: research/SEALS.md in the research-audit worktree
(D:/cryptoscreener-worktrees/research-audit, branch research/audit-2026-10-20, 7e92b67).
Its text, as of that commit:

    # Sealed windows

    Written 2026-10-19 (UTC) under constitution v2 C3, owner ruling R3.
    - Scopes may be changed only by the owner (C4).
    - **Global research holdout (C3a):** no OHLCV value, or anything derived from it, after
      2026-07-14 is used by any research.
    - **Study seals (C3b):** each row below seals its window only for the strategies of the
      study that reserved it.
    - **Metadata (C3c):** row counts, min/max timestamps and gap detection are allowed on any
      window.

    Sources: the trial ledger (`D:/cryptoscreener-worktrees/trial-ledger/backend/app/research/
    trial_ledger/reservations.csv`), code guards and study docs, as cited.

    ## Seals in force

    | # | Window (UTC, end exclusive) | Coins | TF | Study | Strategies sealed | Source |
    | S0 | 2026-07-15 -> | all | all | global research holdout (Regime Lab, daily/MTF screens,
         daily tab, book 16 holdout) | every research strategy (C3a) | reservations.csv
         `regime_lab`; `backend/app/research/engine_backtest.py` `DAILY_TREND_HOLDOUT_START`;
         `backend/app/api/routes.py:988` |
    | S1 | 2024-07-01 -> 2026-01-01 | BTCUSDT | all | DLE BTC study | the DLE 4h/1h/15m
         price-action method (not on the Backtest menu) | reservations.csv `dle_btc` |
    | S2 | 2026-01-08 -> 2026-10-15 | BTCUSDT, ETHUSDT, SOLUSDT | all | structure study (H1-H8)
         | the structure study's rule families in `research/` (not on the menu) |
         reservations.csv `structure_h1h8` |
    | S3 | 2026-01-08 -> 2026-10-15 | BTCUSDT | all | BTC intraday study | intraday study
         tracks A/B/C and their two detectors (not on the menu) | reservations.csv
         `btc_intraday` |
    | S4 | 2024-01-01 -> 2026-01-01 (VAL); 2026-01-08 -> 2026-07-15 (FINAL) | BTCUSDT, ETHUSDT,
         SOLUSDT | 1m-4h | BOS/zone AVP study (BZA) | the BZA zone x side cells (not on the
         menu) | bos-zone-avp docs and `data.guard`; not in the ledger |
    | S5 | 2025-07-01 -> 2026-07-15, mid and small tiers only | regime-book mid/small universe
         | 1h/4h | regime-book study | the regime-book classifier book (not on the menu); its
         large-tier validation was already read once | regime-book worktree `config.yaml` |
    | S6 | 2026-10-06 -> 2027-04-01 | BTCUSDT, ETHUSDT, SOLUSDT | 1h | COMBO_02 v1.1 forward
         acceptance | COMBO_02_V1_1_RISK_CONTROLLED and its parent COMBO_02_V1 signals |
         reservations.csv `combo02_v1_1` |
    | S7 | 2026-10-16 -> 2027-10-16 | BTCUSDT | 1h | CANDLE12 V3 OOS-2 | CANDLE12_V3 /
         CANDLE12_V3_EXEC_FIX | reservations.csv `candle12_v3_oos2` |
    | S8 | 2026-10-16 -> | ETHUSDT | 1h | LCSC FWD-3 | LARGE_CAP_SWEEP_CHOCH (ETH) |
         reservations.csv `lcsc_fwd3` |
    | S9 | 2026-10-19 -> | BTCUSDT, ETHUSDT | 4h | book 16 FWD-1 | MTF_4H_16_BREAKOUT252 |
         reservations.csv `book16_fwd1` |
    | S10 | from its activation bar (not yet active) | 27 cap-book coins | 1h | passed-book
          forward test PASSED_BOOK_FWD-1 | the passed book (10 PASSED_SIDES) |
          `docs/passed_book_fwd_rule.md` |
    | S11 | 2026-07-15 -> 2026-10-08 | 27 cap-book coins | all | final holdout (owner ruling
          D15b, GATES E13) | one run per Gate 3 passer | `research/HOLDOUT_RUNS.csv` |

    S6-S10 lie after 2026-07-14, so S0 already covers them for research.

    ## Contamination that existed before this run

    The 10 Oct passed-book selection (`backend/scripts/run_cap_mtf_risk.py`, outputs
    `research_sandbox/cap_mtf_risk_20261010/`) chose the 10 PASSED_SIDES on the last 480 1h
    bars before 2026-10-10. That is about 2026-09-20 -> 2026-10-10, inside S0.
    - So the bars the passed book was chosen on are already used.
    - The passed book's only clean test is its forward test (S10).
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence

# --- the seal ----------------------------------------------------------------------------------

#: Last UTC day any backtest may read (inclusive). SEALS.md S0 / constitution C3a.
SEAL_LAST_DAY = "2026-07-14"
SEAL_LAST_DATE = date(2026, 7, 14)
#: First instant that is sealed: no bar opening at or after it is loaded.
SEAL_END_EXCLUSIVE = datetime(2026, 7, 15, tzinfo=timezone.utc)
#: 2026-07-14 23:59 UTC, the latest minute a backtest data load may end on.
SEAL_LAST_MINUTE = SEAL_END_EXCLUSIVE - timedelta(minutes=1)

SEALED_CODE = "sealed_holdout"
SEALED_MESSAGE = "Sealed: data after 2026-07-14 is reserved for the holdout (SEALS.md)"

#: The Profit checker's result (cap/MTF risk book, 1h 20 Sep - 10 Oct 2026, 4h 31 Aug - 10 Oct).
PROFIT_CHECKER_WINDOW = {"1h": ("2026-09-20", "2026-10-10"), "4h": ("2026-08-31", "2026-10-10")}
PROFIT_CHECKER_SEALED_LABEL = (
    "Inside the sealed holdout window; already used before the seal (SEALS.md). Not evidence."
)

#: HTTP paths that run a backtest. ``SealGuardMiddleware`` opens ``sealed_scope`` for each.
SEALED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/research/long-strategy/backtest",  # single strategy, daily books, book 16, hedge, loss analysis
    "/api/research/regime-backtest",  # passed / auto regime book
    "/api/research/ohlcv-candles",  # Backtest-tab trade charts and the stop-out replay
    "/api/research/cap-book-agents",  # Profit checker (read-only files; its re-run is refused)
    "/api/research/bos-combinations",  # BOS combination backtests
    "/api/research/multi-cap-strategies",  # multi-cap research runs
    "/api/grid-trading/backtest",
    "/api/grid-trading/validate",
    "/api/grid-trading/scan",
)


def is_sealed_path(path: str) -> bool:
    """True for a backtest HTTP path (``/api/signals/{symbol}/backtest`` included)."""
    p = str(path or "")
    if p.startswith("/api/signals/") and p.rstrip("/").endswith("/backtest"):
        return True
    return any(p == prefix or p.startswith(prefix + "/") or p.startswith(prefix + "?") for prefix in SEALED_PATH_PREFIXES)


# --- errors and request checks ------------------------------------------------------------------


class SealedRangeError(ValueError):
    """An explicit request date lies after the seal. Routes answer HTTP 400 with SEALED_MESSAGE."""

    code = SEALED_CODE
    status_code = 400

    def __init__(self, message: str = SEALED_MESSAGE) -> None:
        super().__init__(message)


def _day(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        # Unreadable dates are the callers' own validation errors, not a seal question.
        return None


def check_request_dates(start_date: Any = None, end_date: Any = None) -> None:
    """Refuse a calendar request that reaches past the seal. Never clamps an explicit date.

    ``end_date`` is the inclusive UI day; a day after 2026-07-14 is refused. A start day after
    2026-07-14 is refused too (the whole window would be sealed). Missing dates pass: a missing
    end is closed at the seal by the loaders.
    """
    end = _day(end_date)
    if end is not None and end > SEAL_LAST_DATE:
        raise SealedRangeError()
    start = _day(start_date)
    if start is not None and start > SEAL_LAST_DATE:
        raise SealedRangeError()


def http_sealed_error(exc: SealedRangeError | None = None):
    """The HTTPException a route raises for a sealed request (400, the exact message)."""
    from fastapi import HTTPException

    return HTTPException(status_code=400, detail=str(exc) if exc else SEALED_MESSAGE)


# --- clamps -------------------------------------------------------------------------------------


def clamp_end_exclusive(end_exclusive: datetime | None) -> datetime:
    """The earlier of ``end_exclusive`` and the seal; a missing end is the seal."""
    if end_exclusive is None:
        return SEAL_END_EXCLUSIVE
    end = end_exclusive if end_exclusive.tzinfo else end_exclusive.replace(tzinfo=timezone.utc)
    return min(end, SEAL_END_EXCLUSIVE)


def clamp_end_day(end_day: str | None) -> str:
    """The earlier of an inclusive UI end day and SEAL_LAST_DAY (for an implicit end only)."""
    day = _day(end_day)
    if day is None or day > SEAL_LAST_DATE:
        return SEAL_LAST_DAY
    return day.isoformat()


def bar_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("time") if isinstance(candle, Mapping) else None
    if raw is None and isinstance(candle, Mapping):
        raw = candle.get("timestamp") or candle.get("open_time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def clamp_candles(candles: Sequence[Mapping[str, Any]] | None) -> list[Any]:
    """Bars that open before the seal. A bar with no readable time is dropped too."""
    out: list[Any] = []
    for candle in candles or []:
        ts = bar_time(candle)
        if ts is not None and ts < SEAL_END_EXCLUSIVE:
            out.append(candle)
    return out


def bar_span(candles: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """First and last bar actually loaded (warm-up included), as ISO UTC, for the run record."""
    series = list(candles or [])
    first = bar_time(series[0]) if series else None
    last = bar_time(series[-1]) if series else None
    return {
        "first_bar": first.isoformat() if first else None,
        "last_bar": last.isoformat() if last else None,
        "bars": len(series),
        "sealed_through": SEAL_LAST_DAY,
    }


def merge_spans(spans: Sequence[Mapping[str, Any] | None]) -> dict[str, Any]:
    """Earliest first bar and latest last bar over several loads."""
    firsts = [s["first_bar"] for s in spans if s and s.get("first_bar")]
    lasts = [s["last_bar"] for s in spans if s and s.get("last_bar")]
    return {
        "first_bar": min(firsts) if firsts else None,
        "last_bar": max(lasts) if lasts else None,
        "sealed_through": SEAL_LAST_DAY,
    }


# --- scope --------------------------------------------------------------------------------------

_SEALED: contextvars.ContextVar[bool] = contextvars.ContextVar("backtest_sealed", default=False)


def seal_active() -> bool:
    """True inside ``sealed_scope`` (a backtest request or job)."""
    return _SEALED.get()


@contextmanager
def sealed_scope() -> Iterator[None]:
    """Mark the current task (and tasks/threads it starts) as a sealed backtest."""
    token = _SEALED.set(True)
    try:
        yield
    finally:
        _SEALED.reset(token)


def sealed(fn: Any) -> Any:
    """Decorator: run an async backtest entry point inside ``sealed_scope``.

    ``functools.wraps`` keeps the wrapped signature (``inspect.signature`` follows it).
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        with sealed_scope():
            return await fn(*args, **kwargs)

    return wrapper


class SealGuardMiddleware:
    """Pure ASGI middleware: backtest paths run inside ``sealed_scope``.

    A pure middleware (not BaseHTTPMiddleware) runs the endpoint in this same context, and
    ``asyncio.create_task`` copies it, so a background job started by the request is sealed too.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and is_sealed_path(str(scope.get("path") or "")):
            with sealed_scope():
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
