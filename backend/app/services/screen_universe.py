"""Main screener display universe — max 100 dynamically selected symbols.

Presentation / screening ranking only. Does NOT modify signal, structure,
entry/SL/TP, or market-signal engines. Does NOT shrink backend ingestion.

Components of screen_priority_score (no profitability / no future data):
  DATA_QUALITY      — live price, OHLCV/structure/RVOL/zones readiness
  MARKET_LIQUIDITY  — live Binance quote_volume_24h (log-scaled)
  STRUCTURE_READINESS — trend / BOS / impulse / pullback availability
  SIGNAL_READINESS  — directional market_signal (BUY/SELL), not WAITING boost
  SETUP_READINESS   — ENTRY_READY / entry candidates / setup context

WAITING is never treated as a positive setup. ENTRY_READY is never inferred
from BUY + NO_SETUP.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models.schemas import DataStatus, FreshValue, ScreenerRow

MAX_SCREEN_SYMBOLS = 100
SCREEN_SIZE_OPTIONS = (25, 50, 100)
# Re-evaluate membership periodically — not on every price tick.
SELECTION_CACHE_TTL_SEC = 12.0
# Near-tie band for anti-flicker (UI stability only).
SCORE_TIE_EPSILON = 0.5

# Screen filters — operate on existing fields only.
SCREEN_FILTER_ALL = "ALL_ELIGIBLE"
SCREEN_FILTER_SETUPS = "SETUPS"
SCREEN_FILTER_ENTRY_READY = "ENTRY_READY"
SCREEN_FILTER_BUY_BIAS = "BUY_BIAS"
SCREEN_FILTER_SELL_BIAS = "SELL_BIAS"
SCREEN_FILTER_WAITING = "WAITING"
SCREEN_FILTER_CONFLICT = "CONFLICT"

ALLOWED_SCREEN_FILTERS = frozenset(
    {
        SCREEN_FILTER_ALL,
        SCREEN_FILTER_SETUPS,
        SCREEN_FILTER_ENTRY_READY,
        SCREEN_FILTER_BUY_BIAS,
        SCREEN_FILTER_SELL_BIAS,
        SCREEN_FILTER_WAITING,
        SCREEN_FILTER_CONFLICT,
        # Aliases accepted from UI
        "ALL",
        "all_eligible",
        "setups",
        "entry_ready",
        "buy_bias",
        "sell_bias",
        "waiting",
        "conflict",
    }
)


def clamp_screen_limit(limit: int | None) -> int:
    """Hard cap at MAX_SCREEN_SYMBOLS; default 100."""
    if limit is None:
        return MAX_SCREEN_SYMBOLS
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return MAX_SCREEN_SYMBOLS
    return max(1, min(n, MAX_SCREEN_SYMBOLS))


def normalize_screen_filter(raw: str | None) -> str:
    if not raw:
        return SCREEN_FILTER_ALL
    s = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "ALL": SCREEN_FILTER_ALL,
        "ALL_ELIGIBLE": SCREEN_FILTER_ALL,
        "SETUPS": SCREEN_FILTER_SETUPS,
        "SETUP": SCREEN_FILTER_SETUPS,
        "ENTRY_READY": SCREEN_FILTER_ENTRY_READY,
        "BUY_BIAS": SCREEN_FILTER_BUY_BIAS,
        "SELL_BIAS": SCREEN_FILTER_SELL_BIAS,
        "WAITING": SCREEN_FILTER_WAITING,
        "CONFLICT": SCREEN_FILTER_CONFLICT,
    }
    return aliases.get(s, SCREEN_FILTER_ALL)


def _fv_status(fv: Any) -> str:
    if fv is None:
        return DataStatus.UNAVAILABLE.value
    if isinstance(fv, FreshValue):
        return str(fv.status.value if hasattr(fv.status, "value") else fv.status).upper()
    if isinstance(fv, dict):
        return str(fv.get("status") or DataStatus.UNAVAILABLE.value).upper()
    return DataStatus.UNAVAILABLE.value


def _fv_value(fv: Any) -> Any:
    if fv is None:
        return None
    if isinstance(fv, FreshValue):
        return fv.value
    if isinstance(fv, dict):
        return fv.get("value")
    return fv


def _str_field(row: ScreenerRow, name: str) -> str:
    raw = _fv_value(getattr(row, name, None))
    return str(raw or "").upper().strip()


def _is_liveish(status: str) -> bool:
    return status in {
        DataStatus.LIVE.value,
        DataStatus.HISTORICAL.value,
        DataStatus.CACHED.value,
    }


def _has_numeric(fv: Any) -> bool:
    v = _fv_value(fv)
    if v is None:
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


@dataclass
class ExclusionCounts:
    excluded_missing_price: int = 0
    excluded_stale: int = 0
    excluded_unavailable: int = 0
    excluded_missing_ohlcv: int = 0
    excluded_bad_data_status: int = 0
    excluded_screen_filter: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "excluded_missing_price": self.excluded_missing_price,
            "excluded_stale": self.excluded_stale,
            "excluded_unavailable": self.excluded_unavailable,
            "excluded_missing_ohlcv": self.excluded_missing_ohlcv,
            "excluded_bad_data_status": self.excluded_bad_data_status,
            "excluded_screen_filter": self.excluded_screen_filter,
        }

    @property
    def total_excluded(self) -> int:
        return sum(self.as_dict().values())


@dataclass
class ScoredRow:
    row: ScreenerRow
    score: float
    reason: str
    components: dict[str, float] = field(default_factory=dict)


@dataclass
class ScreenSelectionResult:
    rows: list[ScreenerRow]
    total_universe: int
    eligible_count: int
    returned_count: int
    limit: int
    selection_updated_at: str
    excluded: dict[str, int]
    search_mode: bool = False
    screen_filter: str = SCREEN_FILTER_ALL
    # For callers that previously used `total` as filtered length before page:
    filtered_before_limit: int = 0


def hard_eligibility(
    row: ScreenerRow,
    *,
    ohlcv_ready: bool | None = None,
    exclusions: ExclusionCounts | None = None,
) -> bool:
    """Required before a symbol may occupy a primary screen slot.

    Reuses existing freshness statuses — no new volume/data thresholds invented.
    """
    ex = exclusions
    price = getattr(row, "price", None)
    price_st = _fv_status(price)
    price_v = _fv_value(price)

    if row.data_status == DataStatus.UNAVAILABLE:
        if ex is not None:
            ex.excluded_unavailable += 1
        return False

    if price_v is None or not _has_numeric(price):
        if ex is not None:
            ex.excluded_missing_price += 1
        return False

    if price_st == DataStatus.UNAVAILABLE.value:
        if ex is not None:
            ex.excluded_unavailable += 1
        return False

    if price_st == DataStatus.STALE.value:
        if ex is not None:
            ex.excluded_stale += 1
        return False

    # Prefer symbols with enough 15m OHLCV to run existing analysis.
    # When ohlcv_ready is provided by caller (setup_ohlcv_ready), use it.
    # Fallback: structure/RVOL still WAITING with methodology implying missing OHLCV
    # is treated as missing when we can detect it; otherwise do not invent readiness.
    if ohlcv_ready is False:
        if ex is not None:
            ex.excluded_missing_ohlcv += 1
        return False

    if ohlcv_ready is None:
        # Soft structural proxy only when explicit readiness unknown:
        # if relative_volume and structure both UNAVAILABLE with no values, exclude.
        rvol_st = _fv_status(getattr(row, "relative_volume", None))
        struct_st = _fv_status(getattr(row, "structure", None))
        if (
            rvol_st == DataStatus.UNAVAILABLE.value
            and struct_st == DataStatus.UNAVAILABLE.value
            and not _has_numeric(getattr(row, "relative_volume", None))
            and _fv_value(getattr(row, "structure", None)) is None
        ):
            if ex is not None:
                ex.excluded_missing_ohlcv += 1
            return False

    return True


def matches_screen_filter(row: ScreenerRow, screen_filter: str) -> bool:
    """Filter using existing market_signal / setup_signal fields only."""
    f = normalize_screen_filter(screen_filter)
    setup = _str_field(row, "setup_signal")
    market = _str_field(row, "market_signal")

    if f == SCREEN_FILTER_ALL:
        return True
    if f == SCREEN_FILTER_SETUPS:
        return setup in {
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
            "ENTRY_READY",
            "ENTRY_CANDIDATE",
            "NO_SETUP",
            "CONFLICT",
            "INVALIDATED",
        } and setup not in {"", "WAITING"}
    if f == SCREEN_FILTER_ENTRY_READY:
        # Never promote WAITING or BUY+NO_SETUP to ENTRY_READY.
        return setup in {
            "ENTRY_READY",
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
            "ENTRY_CANDIDATE",
        }
    if f == SCREEN_FILTER_BUY_BIAS:
        return market in {"BUY", "STRONG_BUY"}
    if f == SCREEN_FILTER_SELL_BIAS:
        return market in {"SELL", "STRONG_SELL"}
    if f == SCREEN_FILTER_WAITING:
        return setup in {"", "WAITING"} or market in {"", "WAITING"}
    if f == SCREEN_FILTER_CONFLICT:
        return setup == "CONFLICT"
    return True


def _data_quality_score(row: ScreenerRow) -> tuple[float, list[str]]:
    """0–40. Prefer symbols where existing analysis can run."""
    score = 0.0
    tags: list[str] = []
    if _is_liveish(_fv_status(row.price)) and _has_numeric(row.price):
        score += 8.0
    if _is_liveish(_fv_status(row.quote_volume_24h)) and _has_numeric(row.quote_volume_24h):
        score += 4.0
    if _is_liveish(_fv_status(row.relative_volume)) and _has_numeric(row.relative_volume):
        score += 8.0
        tags.append("DATA_READY")
    if _is_liveish(_fv_status(row.structure)) and _fv_value(row.structure):
        score += 8.0
    bos = _fv_value(getattr(row, "setup_bos", None)) or _fv_value(getattr(row, "bos", None))
    if bos and _is_liveish(_fv_status(getattr(row, "setup_bos", None) or row.bos)):
        score += 6.0
    zone_ok = _is_liveish(_fv_status(row.zone)) and _fv_value(row.zone)
    if zone_ok:
        score += 6.0
    if not tags and score >= 20:
        tags.append("DATA_READY")
    return min(score, 40.0), tags


def _liquidity_score(row: ScreenerRow) -> tuple[float, list[str]]:
    """0–20 from live quote_volume_24h. Not market-cap ranking."""
    tags: list[str] = []
    if not _has_numeric(row.quote_volume_24h):
        return 0.0, tags
    if not _is_liveish(_fv_status(row.quote_volume_24h)):
        return 0.0, tags
    vol = float(_fv_value(row.quote_volume_24h) or 0.0)
    if vol <= 0:
        return 0.0, tags
    # log10 scale: 1e5→~1, 1e7→~5, 1e9→~9 → map into 0–20
    raw = math.log10(vol + 1.0)
    score = max(0.0, min(20.0, (raw - 4.0) * 4.0))
    if score >= 14.0:
        tags.append("HIGH_LIQUIDITY")
    return score, tags


def _structure_readiness_score(row: ScreenerRow) -> tuple[float, list[str]]:
    """0–15 confirmed trend / BOS / impulse / pullback."""
    score = 0.0
    tags: list[str] = []
    trend = _str_field(row, "setup_trend") or _str_field(row, "structure")
    if trend in {"BULLISH", "BULLISH_STRUCTURE", "HH_HL", "UPTREND"}:
        score += 6.0
        tags.append("BULLISH_STRUCTURE")
    elif trend in {"BEARISH", "BEARISH_STRUCTURE", "LH_LL", "DOWNTREND"}:
        score += 6.0
        tags.append("BEARISH_STRUCTURE")
    elif trend and trend not in {"WAITING", "INSUFFICIENT_DATA", "NEUTRAL", ""}:
        score += 3.0

    bos = _str_field(row, "setup_bos") or _str_field(row, "bos")
    if bos and bos not in {"WAITING", "NONE", ""}:
        score += 5.0
    impulse = _str_field(row, "setup_impulse")
    if impulse in {"STRONG", "MODERATE", "WEAK"}:
        score += 2.0
    pullback = _str_field(row, "setup_pullback")
    if pullback in {"ACTIVE", "CONFIRMED"}:
        score += 2.0
    return min(score, 15.0), tags


def _signal_readiness_score(row: ScreenerRow) -> tuple[float, list[str]]:
    """0–15. Directional market_signal with setup context. WAITING gets ~0."""
    market = _str_field(row, "market_signal")
    setup = _str_field(row, "setup_signal")
    score = 0.0
    tags: list[str] = []
    if market in {"STRONG_BUY", "STRONG_SELL"}:
        score += 8.0
    elif market in {"BUY", "SELL"}:
        score += 6.0
    elif market == "NEUTRAL":
        score += 2.0
    # WAITING / empty → no positive boost
    if setup in {
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
        "ENTRY_READY",
        "ENTRY_CANDIDATE",
    }:
        score += 7.0
    elif setup == "NO_SETUP" and market in {"BUY", "SELL", "STRONG_BUY", "STRONG_SELL"}:
        # Valid directional signal with no setup — not ENTRY_READY
        score += 2.0
    return min(score, 15.0), tags


def _setup_readiness_score(row: ScreenerRow) -> tuple[float, list[str]]:
    """0–30 screen-priority hierarchy from existing setup_signal only."""
    setup = _str_field(row, "setup_signal")
    tags: list[str] = []
    if setup == "ENTRY_READY":
        tags.append("ENTRY_READY")
        return 30.0, tags
    if setup == "LONG_ENTRY_CANDIDATE":
        tags.append("LONG_ENTRY_CANDIDATE")
        return 28.0, tags
    if setup == "SHORT_ENTRY_CANDIDATE":
        tags.append("SHORT_ENTRY_CANDIDATE")
        return 28.0, tags
    if setup == "ENTRY_CANDIDATE":
        tags.append("LONG_ENTRY_CANDIDATE")
        return 26.0, tags
    if setup == "CONFLICT":
        return 4.0, tags
    if setup == "INVALIDATED":
        return 3.0, tags
    if setup == "NO_SETUP":
        return 5.0, tags
    # WAITING — never positive priority as if it were a setup
    return 0.0, tags


def compute_screen_priority(row: ScreenerRow) -> ScoredRow:
    """Numeric presentation ranking from existing row fields only."""
    dq, t1 = _data_quality_score(row)
    liq, t2 = _liquidity_score(row)
    st, t3 = _structure_readiness_score(row)
    sig, t4 = _signal_readiness_score(row)
    su, t5 = _setup_readiness_score(row)
    total = dq + liq + st + sig + su
    tags = t5 or t4 or t3 or t2 or t1
    reason = tags[0] if tags else "DATA_READY"
    # Guard: never claim ENTRY_READY unless setup_signal says so
    if reason == "ENTRY_READY" and _str_field(row, "setup_signal") != "ENTRY_READY":
        reason = _str_field(row, "setup_signal") or "DATA_READY"
    return ScoredRow(
        row=row,
        score=float(total),
        reason=reason,
        components={
            "DATA_QUALITY": dq,
            "MARKET_LIQUIDITY": liq,
            "STRUCTURE_READINESS": st,
            "SIGNAL_READINESS": sig,
            "SETUP_READINESS": su,
        },
    )


class ScreenUniverseSelector:
    """Selects ≤ MAX_SCREEN_SYMBOLS with brief cache + anti-flicker ordering."""

    def __init__(self, *, ttl_sec: float = SELECTION_CACHE_TTL_SEC) -> None:
        self.ttl_sec = ttl_sec
        self._lock = threading.Lock()
        self._prev_order: dict[str, int] = {}
        self._cache_key: str | None = None
        self._cache_at: float = 0.0
        self._cached_symbols: list[str] = []
        self._last_meta: dict[str, Any] = {}

    def clear_cache(self) -> None:
        with self._lock:
            self._cache_key = None
            self._cached_symbols = []
            self._cache_at = 0.0

    def last_meta(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_meta)

    def select(
        self,
        rows: Iterable[ScreenerRow],
        *,
        total_universe: int,
        limit: int = MAX_SCREEN_SYMBOLS,
        screen_filter: str = SCREEN_FILTER_ALL,
        search: str | None = None,
        ohlcv_ready_fn: Any | None = None,
        force_refresh: bool = False,
        apply_screen_cap: bool = True,
    ) -> ScreenSelectionResult:
        """Select screening universe.

        search mode: find any matching symbol in the full candidate list
        (bypass top-100 membership). Does not permanently mutate the cache.
        """
        limit = clamp_screen_limit(limit) if apply_screen_cap else max(1, int(limit or 1))
        filt = normalize_screen_filter(screen_filter)
        now = time.monotonic()
        now_iso = datetime.now(timezone.utc).isoformat()
        all_rows = list(rows)
        search_q = (search or "").strip().upper()
        search_mode = bool(search_q)

        exclusions = ExclusionCounts()
        eligible: list[ScreenerRow] = []

        for row in all_rows:
            if search_mode:
                # Search: any matching Binance symbol (outside top-100 ok).
                if search_q not in row.symbol.upper() and search_q not in (
                    row.base_asset or ""
                ).upper():
                    continue
                eligible.append(row)
                continue

            ohlcv_ready = None
            if ohlcv_ready_fn is not None:
                try:
                    ohlcv_ready = bool(ohlcv_ready_fn(row.symbol))
                except Exception:  # noqa: BLE001
                    ohlcv_ready = None
            if not hard_eligibility(row, ohlcv_ready=ohlcv_ready, exclusions=exclusions):
                continue
            if not matches_screen_filter(row, filt):
                exclusions.excluded_screen_filter += 1
                continue
            eligible.append(row)

        scored = [compute_screen_priority(r) for r in eligible]

        with self._lock:
            prev = dict(self._prev_order)

            def sort_key(s: ScoredRow) -> tuple:
                # Higher score first; near-ties preserve previous order
                prev_rank = prev.get(s.row.symbol, 10_000)
                # Bucket score to reduce flicker from tiny liquidity changes
                bucket = round(s.score / SCORE_TIE_EPSILON) * SCORE_TIE_EPSILON
                return (-bucket, prev_rank, -(s.score), s.row.symbol)

            scored.sort(key=sort_key)

            if search_mode:
                # Search: return matches (capped), do not rewrite top-100 cache.
                page_scored = scored[:limit]
                out_rows = []
                for i, s in enumerate(page_scored, start=1):
                    s.row.screen_priority_score = s.score
                    s.row.screen_priority_reason = s.reason
                    s.row.rank = i
                    s.row.screener_rank = i
                    out_rows.append(s.row)
                meta = {
                    "total_universe": total_universe,
                    "eligible_count": len(eligible),
                    "returned_count": len(out_rows),
                    "limit": limit,
                    "selection_updated_at": now_iso,
                    "excluded": exclusions.as_dict(),
                    "search_mode": True,
                    "screen_filter": filt,
                    "note": "Search result — not a permanent top-100 membership change",
                }
                self._last_meta = meta
                return ScreenSelectionResult(
                    rows=out_rows,
                    total_universe=total_universe,
                    eligible_count=len(eligible),
                    returned_count=len(out_rows),
                    limit=limit,
                    selection_updated_at=now_iso,
                    excluded=exclusions.as_dict(),
                    search_mode=True,
                    screen_filter=filt,
                    filtered_before_limit=len(eligible),
                )

            cache_key = f"{filt}|{limit}|{total_universe}"
            use_cache = (
                not force_refresh
                and self._cache_key == cache_key
                and (now - self._cache_at) < self.ttl_sec
                and self._cached_symbols
            )
            if use_cache:
                by_sym = {s.row.symbol: s for s in scored}
                ordered_syms = [s for s in self._cached_symbols if s in by_sym]
                # Append newly eligible high-priority symbols not in cache
                for s in scored:
                    if s.row.symbol not in ordered_syms:
                        ordered_syms.append(s.row.symbol)
                page_syms = ordered_syms[:limit]
                page_scored = [by_sym[s] for s in page_syms if s in by_sym]
                selection_ts = self._last_meta.get("selection_updated_at") or now_iso
            else:
                page_scored = scored[:limit]
                self._cached_symbols = [s.row.symbol for s in page_scored]
                self._cache_key = cache_key
                self._cache_at = now
                selection_ts = now_iso
                self._prev_order = {
                    s.row.symbol: i for i, s in enumerate(page_scored)
                }

            out_rows = []
            for i, s in enumerate(page_scored, start=1):
                s.row.screen_priority_score = s.score
                s.row.screen_priority_reason = s.reason
                s.row.rank = i
                s.row.screener_rank = i
                out_rows.append(s.row)

            meta = {
                "total_universe": total_universe,
                "eligible_count": len(eligible),
                "returned_count": len(out_rows),
                "limit": limit,
                "selection_updated_at": selection_ts,
                "excluded": exclusions.as_dict(),
                "search_mode": False,
                "screen_filter": filt,
            }
            self._last_meta = meta
            return ScreenSelectionResult(
                rows=out_rows,
                total_universe=total_universe,
                eligible_count=len(eligible),
                returned_count=len(out_rows),
                limit=limit,
                selection_updated_at=selection_ts,
                excluded=exclusions.as_dict(),
                search_mode=False,
                screen_filter=filt,
                filtered_before_limit=len(eligible),
            )


# Process-wide selector (anti-flicker + TTL across HTTP/WS requests)
_selector = ScreenUniverseSelector()


def get_screen_universe_selector() -> ScreenUniverseSelector:
    return _selector
