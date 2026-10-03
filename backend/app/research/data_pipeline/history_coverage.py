"""History availability audit for BOS multi-timeframe research.

Read-only against PostgreSQL OHLCV. Never fabricates, interpolates, or fills gaps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.research.data_pipeline.config import BOS_MIN_TIMEFRAMES, DEFAULT_PERIOD_START
from app.services.database import db_manager

REQUIRED_TFS: tuple[str, ...] = tuple(BOS_MIN_TIMEFRAMES)  # 5m, 15m, 1h, 4h

# Year-bucket labels for common MTF span (actual depth, not requested period).
_YEAR_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("6y+", 6.0, None),
    ("5y", 5.0, 6.0),
    ("4y", 4.0, 5.0),
    ("3y", 3.0, 4.0),
    ("2y", 2.0, 3.0),
    ("<2y", 0.0, 2.0),
)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _years_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() / (86400.0 * 365.25))


def _bucket_label(years: float | None) -> str | None:
    if years is None:
        return None
    for label, lo, hi in _YEAR_BUCKETS:
        if hi is None:
            if years >= lo:
                return label
        elif lo <= years < hi:
            return label
    return "<2y"


async def audit_symbol_mtf_coverage(
    *,
    timeframes: Sequence[str] = REQUIRED_TFS,
    period_start: str = DEFAULT_PERIOD_START,
) -> dict[str, Any]:
    """Per-symbol first/last for each required TF + common_mtf_first_timestamp."""
    tfs = [str(t).lower() for t in timeframes]
    if not db_manager.enabled or db_manager.engine is None:
        return {
            "status": "DATABASE_UNAVAILABLE",
            "symbols": [],
            "by_timeframe": {},
            "year_buckets": {},
            "requested_period_start": period_start,
            "note": "PostgreSQL unavailable — no coverage audit.",
        }

    async with db_manager.engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT symbol, timeframe, COUNT(*) AS n,
                       MIN(time) AS first_ts, MAX(time) AS last_ts
                FROM ohlcv
                WHERE timeframe = ANY(:tfs)
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
                """
            ),
            {"tfs": tfs},
        )
        rows = result.fetchall()

    by_sym: dict[str, dict[str, dict[str, Any]]] = {}
    by_tf: dict[str, dict[str, Any]] = {
        tf: {
            "symbols": 0,
            "candles": 0,
            "first_timestamp": None,
            "last_timestamp": None,
        }
        for tf in tfs
    }

    for symbol, tf, n, first_ts, last_ts in rows:
        sym = str(symbol).upper()
        tf_s = str(tf).lower()
        first_iso = first_ts.isoformat() if first_ts else None
        last_iso = last_ts.isoformat() if last_ts else None
        cell = {
            "candle_count": int(n),
            "first": first_iso,
            "last": last_iso,
        }
        by_sym.setdefault(sym, {})[tf_s] = cell
        agg = by_tf.setdefault(
            tf_s,
            {"symbols": 0, "candles": 0, "first_timestamp": None, "last_timestamp": None},
        )
        agg["symbols"] += 1
        agg["candles"] += int(n)
        if first_iso and (
            agg["first_timestamp"] is None or first_iso < agg["first_timestamp"]
        ):
            agg["first_timestamp"] = first_iso
        if last_iso and (
            agg["last_timestamp"] is None or last_iso > agg["last_timestamp"]
        ):
            agg["last_timestamp"] = last_iso

    symbols_out: list[dict[str, Any]] = []
    bucket_counts = {label: 0 for label, _, _ in _YEAR_BUCKETS}
    bucket_counts["no_common_mtf"] = 0
    mtf_ready = 0

    for sym in sorted(by_sym):
        tmap = by_sym[sym]
        row: dict[str, Any] = {"symbol": sym}
        firsts: list[datetime] = []
        lasts: list[datetime] = []
        missing: list[str] = []
        for tf in tfs:
            info = tmap.get(tf)
            row[f"{tf}_first"] = info.get("first") if info else None
            row[f"{tf}_last"] = info.get("last") if info else None
            row[f"{tf}_candles"] = int(info["candle_count"]) if info else 0
            if not info:
                missing.append(tf)
                continue
            f = _parse_iso(info.get("first"))
            l = _parse_iso(info.get("last"))
            if f:
                firsts.append(f)
            if l:
                lasts.append(l)

        if missing or len(firsts) < len(tfs) or len(lasts) < len(tfs):
            row["common_mtf_first_timestamp"] = None
            row["common_mtf_last_timestamp"] = None
            row["common_mtf_years"] = None
            row["mtf_ready"] = False
            row["missing_timeframes"] = missing
            row["year_bucket"] = None
            bucket_counts["no_common_mtf"] += 1
        else:
            common_first = max(firsts)
            common_last = min(lasts)
            years = _years_between(common_first, common_last)
            # Usable only if common window is non-empty
            usable = common_first <= common_last
            row["common_mtf_first_timestamp"] = (
                common_first.isoformat() if usable else None
            )
            row["common_mtf_last_timestamp"] = (
                common_last.isoformat() if usable else None
            )
            row["common_mtf_years"] = round(years, 3) if usable and years is not None else None
            row["mtf_ready"] = bool(usable)
            row["missing_timeframes"] = []
            if usable:
                mtf_ready += 1
                label = _bucket_label(years)
                row["year_bucket"] = label
                if label:
                    bucket_counts[label] = bucket_counts.get(label, 0) + 1
            else:
                row["year_bucket"] = None
                bucket_counts["no_common_mtf"] += 1
        symbols_out.append(row)

    # Universe claim guard — never describe all as N years unless true
    years_with_data = [
        s["common_mtf_years"]
        for s in symbols_out
        if s.get("common_mtf_years") is not None
    ]
    min_common = min(years_with_data) if years_with_data else None
    max_common = max(years_with_data) if years_with_data else None
    all_have_6y = bool(years_with_data) and all(y >= 6.0 for y in years_with_data)

    return {
        "status": "OK",
        "requested_period_start": period_start,
        "required_timeframes": list(tfs),
        "symbol_count": len(symbols_out),
        "mtf_ready_count": mtf_ready,
        "symbols": symbols_out,
        "by_timeframe": by_tf,
        "year_buckets": bucket_counts,
        "common_mtf_span_years": {
            "min": round(min_common, 3) if min_common is not None else None,
            "max": round(max_common, 3) if max_common is not None else None,
            "all_symbols_have_6y": all_have_6y,
        },
        "disclaimer": (
            "Actual history only — depth varies by listing date. "
            "Never claim universe-wide six-year coverage unless all_symbols_have_6y."
        ),
        "fabricated": False,
        "interpolated": False,
    }


async def research_data_readiness_gate(
    *,
    timeframes: Sequence[str] = REQUIRED_TFS,
    period_start: str = DEFAULT_PERIOD_START,
    min_mtf_ready_symbols: int = 3,
    min_common_years: float = 0.5,
    # Full-universe comparison requires a meaningful share with deep MTF history
    min_symbols_with_min_years: int = 50,
    min_years_for_universe: float = 2.0,
    max_duplicate_rate: float = 0.01,
    sample_symbols: Sequence[str] = ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
) -> dict[str, Any]:
    """Hard gate for full strategy comparison.

    Returns status OK or RESEARCH_BLOCKED_DATA_INSUFFICIENT with exact reasons.
    Majors-only smoke validation is separate (explicit symbol list bypasses gate).
    """
    reasons: list[str] = []
    coverage = await audit_symbol_mtf_coverage(
        timeframes=timeframes, period_start=period_start
    )
    if coverage.get("status") != "OK":
        return {
            "status": "RESEARCH_BLOCKED_DATA_INSUFFICIENT",
            "reasons": ["DATABASE_UNAVAILABLE"],
            "coverage": coverage,
            "allow_full_strategy_comparison": False,
        }

    mtf_ready = int(coverage.get("mtf_ready_count") or 0)
    if mtf_ready < min_mtf_ready_symbols:
        reasons.append(
            f"Insufficient MTF-ready symbols: {mtf_ready} < {min_mtf_ready_symbols} "
            f"(need 5m+15m+1h+4h overlapping history)"
        )

    by_tf = coverage.get("by_timeframe") or {}
    for tf in timeframes:
        agg = by_tf.get(str(tf).lower()) or {}
        if int(agg.get("candles") or 0) <= 0:
            reasons.append(f"No candles for required timeframe {tf}")

    deep = 0
    shallow_5m = 0
    for row in coverage.get("symbols") or []:
        years = row.get("common_mtf_years")
        if years is not None and float(years) >= min_years_for_universe:
            deep += 1
        m5_first = _parse_iso(row.get("5m_first"))
        m15_first = _parse_iso(row.get("15m_first"))
        if m5_first and m15_first and m5_first > m15_first:
            # 5m starts later than 15m — still the MTF bottleneck for this symbol
            shallow_5m += 1
    if deep < min_symbols_with_min_years:
        reasons.append(
            f"Only {deep} symbols have >={min_years_for_universe}y common MTF history "
            f"(need >={min_symbols_with_min_years}). "
            "Universe-wide six-year depth is NOT claimed."
        )
    if shallow_5m > max(10, mtf_ready // 2):
        reasons.append(
            f"5m still bottlenecks {shallow_5m} symbols (5m_first later than 15m_first)"
        )

    # Spot-check majors for common depth + series quality
    from app.research.bos_strategy_comparison.data_quality import series_quality_report
    from app.research.postgres_ohlcv import load_ohlcv_series_range

    sample_reports: dict[str, Any] = {}
    for sym in sample_symbols:
        sym_u = sym.upper()
        row = next(
            (s for s in coverage.get("symbols") or [] if s.get("symbol") == sym_u),
            None,
        )
        if not row or not row.get("mtf_ready"):
            reasons.append(f"{sym_u}: missing common MTF history")
            continue
        years = row.get("common_mtf_years")
        if years is None or float(years) < min_common_years:
            reasons.append(
                f"{sym_u}: common MTF history {years}y < {min_common_years}y minimum"
            )
        # Quality sample on setup TF (15m) within common window
        start = _parse_iso(row.get("common_mtf_first_timestamp"))
        end = _parse_iso(row.get("common_mtf_last_timestamp"))
        try:
            candles = await load_ohlcv_series_range(
                sym_u,
                "15m",
                start=start,
                end_exclusive=end,
            )
            # Bound RAM — validate last 20k
            sample = candles[-20_000:] if len(candles) > 20_000 else candles
            q = series_quality_report(sample, "15m")
            sample_reports[sym_u] = {
                "candle_count": len(candles),
                "sample_validated": len(sample),
                "status": q.get("status") or q.get("data_quality"),
                "duplicate_count": q.get("duplicate_count") or q.get("duplicate_candles") or 0,
                "gap_count": q.get("gap_count"),
                "first_available_candle": q.get("first_available_candle"),
                "last_available_candle": q.get("last_available_candle"),
            }
            dup = int(sample_reports[sym_u]["duplicate_count"] or 0)
            n = max(1, len(sample))
            if dup / n > max_duplicate_rate:
                reasons.append(
                    f"{sym_u}: duplicate rate {dup}/{n} exceeds {max_duplicate_rate}"
                )
            if (q.get("status") or q.get("data_quality")) == "INSUFFICIENT_DATA":
                reasons.append(f"{sym_u}: 15m series quality INSUFFICIENT_DATA")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"{sym_u}: quality check failed ({exc})")

    blocked = bool(reasons)
    return {
        "status": (
            "RESEARCH_BLOCKED_DATA_INSUFFICIENT" if blocked else "OK"
        ),
        "allow_full_strategy_comparison": not blocked,
        "reasons": reasons,
        "coverage_summary": {
            "symbol_count": coverage.get("symbol_count"),
            "mtf_ready_count": mtf_ready,
            "symbols_with_min_years": deep,
            "min_years_for_universe": min_years_for_universe,
            "symbols_5m_bottleneck": shallow_5m,
            "year_buckets": coverage.get("year_buckets"),
            "by_timeframe": by_tf,
            "common_mtf_span_years": coverage.get("common_mtf_span_years"),
            "all_symbols_have_6y": (coverage.get("common_mtf_span_years") or {}).get(
                "all_symbols_have_6y"
            ),
        },
        "sample_quality": sample_reports,
        "note": (
            "Gate only — does not run strategy optimization. "
            "Live engines unchanged. No fabricated candles. "
            "Never claim universe-wide six-year coverage unless all_symbols_have_6y."
        ),
    }
