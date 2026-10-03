"""Data-quality and universe eligibility for BOS strategy research.

Never fabricates, interpolates, or fills missing candles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.data_quality import verify_ohlcv
from app.research.postgres_ohlcv import list_symbols_with_ohlcv
from app.services.database import db_manager
from sqlalchemy import text


def _parse_bound(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


async def audit_ohlcv_coverage(
    *,
    timeframes: Sequence[str] = ("4h", "1h", "15m", "5m"),
) -> dict[str, Any]:
    """Per symbol/timeframe candle counts and first/last timestamps from Postgres."""
    if not db_manager.enabled or db_manager.engine is None:
        return {
            "status": "DATABASE_UNAVAILABLE",
            "series": [],
            "by_timeframe": {},
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
            {"tfs": list(timeframes)},
        )
        rows = result.fetchall()

    series: list[dict[str, Any]] = []
    by_tf: dict[str, dict[str, Any]] = {}
    for symbol, tf, n, first_ts, last_ts in rows:
        row = {
            "symbol": str(symbol).upper(),
            "timeframe": str(tf),
            "candle_count": int(n),
            "first_timestamp": first_ts.isoformat() if first_ts else None,
            "last_timestamp": last_ts.isoformat() if last_ts else None,
        }
        series.append(row)
        agg = by_tf.setdefault(
            str(tf),
            {"symbols": 0, "candles": 0, "first_timestamp": None, "last_timestamp": None},
        )
        agg["symbols"] += 1
        agg["candles"] += int(n)
        if first_ts and (
            agg["first_timestamp"] is None or first_ts.isoformat() < agg["first_timestamp"]
        ):
            agg["first_timestamp"] = first_ts.isoformat()
        if last_ts and (
            agg["last_timestamp"] is None or last_ts.isoformat() > agg["last_timestamp"]
        ):
            agg["last_timestamp"] = last_ts.isoformat()

    return {
        "status": "OK",
        "series": series,
        "by_timeframe": by_tf,
        "total_series": len(series),
        "note": "No fabricated candles. Depth is whatever PostgreSQL currently holds.",
    }


def series_quality_report(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    *,
    config: StrategyResearchConfig | None = None,
) -> dict[str, Any]:
    """Wrap shared verify_ohlcv; never fill gaps."""
    cfg = config or StrategyResearchConfig()
    report = verify_ohlcv(candles, timeframe, config=cfg.to_research_config())
    report["fabricated"] = False
    report["interpolated"] = False
    report["synthetic_fill"] = False
    if candles:
        from app.signals._candle_utils import candle_time

        t0 = candle_time(candles[0])
        t1 = candle_time(candles[-1])
        report["first_available_candle"] = t0.isoformat() if t0 else None
        report["last_available_candle"] = t1.isoformat() if t1 else None
        if t0 and t1:
            report["available_history_days"] = (t1 - t0).total_seconds() / 86400.0
    return report


async def build_universe_eligibility(
    *,
    setup_timeframe: str = "15m",
    required_timeframes: Sequence[str] = ("4h", "1h", "15m"),
    config: StrategyResearchConfig | None = None,
    period_start: str | None = None,
) -> dict[str, Any]:
    """Full / eligible / excluded universe with exclusion reasons."""
    cfg = config or StrategyResearchConfig()
    period_start = period_start or cfg.period_start
    start_dt = _parse_bound(period_start)

    if not db_manager.enabled or db_manager.engine is None:
        return {
            "status": "DATABASE_UNAVAILABLE",
            "full_universe": [],
            "eligible_symbols": [],
            "excluded_symbols": [],
            "research_period_requested": {
                "start": period_start,
                "end": "current",
            },
        }

    all_symbols = await list_symbols_with_ohlcv()
    audit = await audit_ohlcv_coverage(timeframes=required_timeframes)
    by_sym: dict[str, dict[str, dict[str, Any]]] = {}
    for row in audit.get("series") or []:
        by_sym.setdefault(row["symbol"], {})[row["timeframe"]] = row

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for sym in all_symbols:
        tfs = by_sym.get(sym, {})
        reasons: list[str] = []
        coverage: dict[str, Any] = {}
        for tf in required_timeframes:
            info = tfs.get(tf)
            if not info:
                reasons.append(f"Missing {tf.upper()} history")
                continue
            coverage[tf] = info
            if int(info["candle_count"]) < (
                cfg.min_bars_setup if tf == setup_timeframe else cfg.min_bars
            ):
                reasons.append(
                    f"Insufficient {tf.upper()} history "
                    f"(n={info['candle_count']})"
                )
            first = _parse_bound(info.get("first_timestamp"))
            if first and start_dt and first > start_dt:
                # Listed later — still eligible from first candle; note only
                coverage[f"{tf}_listed_after_period_start"] = True

        if reasons:
            excluded.append(
                {
                    "symbol": sym,
                    "status": "Excluded",
                    "exclusion_reason": "; ".join(reasons),
                    "coverage": coverage,
                }
            )
        else:
            setup = coverage.get(setup_timeframe) or {}
            eligible.append(
                {
                    "symbol": sym,
                    "status": "Eligible",
                    "first_available_candle": setup.get("first_timestamp"),
                    "last_available_candle": setup.get("last_timestamp"),
                    "candle_count_setup": setup.get("candle_count"),
                    "coverage": coverage,
                    "note": (
                        "Starts from first available historical candle; "
                        "does not assume six-year depth."
                    ),
                }
            )

    return {
        "status": "OK",
        "research_period_requested": {"start": period_start, "end": "current"},
        "full_universe": all_symbols,
        "full_universe_count": len(all_symbols),
        "eligible_symbols": [e["symbol"] for e in eligible],
        "eligible_detail": eligible,
        "eligible_count": len(eligible),
        "excluded_symbols": excluded,
        "excluded_count": len(excluded),
        "required_timeframes": list(required_timeframes),
        "setup_timeframe": setup_timeframe,
        "audit": {
            "by_timeframe": audit.get("by_timeframe"),
            "total_series": audit.get("total_series"),
        },
        "disclaimer": (
            "Research only — live engine unchanged. "
            "Never fabricates candles for missing periods."
        ),
    }
