"""Data quality plus independent 4h / 1h / 15m structure labels.

Research only. Cap group chooses which symbols are listed. It does not set direction.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import get_settings
from app.research.sd_structure.explicit_structure import audit_candles, label_structure_bars
from app.research.service import _load_research_candles
from app.services.database import db_manager
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings

FOCUS = ("BTCUSDT", "LINKUSDT", "SOLUSDT", "TRXUSDT")
TIMEFRAMES = ("4h", "1h", "15m")
LIMITS = {"15m": 5760, "1h": 1440, "4h": 360}


async def _symbols() -> list[tuple[str, str]]:
    async with db_manager.engine.begin() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT DISTINCT ON (symbol) symbol, cap_group
                FROM research_market_cap_observations
                WHERE cap_group = ANY(:groups)
                ORDER BY symbol, effective_time DESC
                """
            ),
            {"groups": ["BTC", "ETH", "large-cap", "mid-cap"]},
        )
        return [(str(row[0]).upper(), str(row[1])) for row in rows.fetchall()]


async def _window_quality(symbol: str, timeframe: str, start: datetime, end: datetime) -> dict:
    step = {"15m": 900, "1h": 3600, "4h": 14400}[timeframe]
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT count(*), count(DISTINCT time), min(time), max(time)
                    FROM ohlcv
                    WHERE symbol = :symbol AND timeframe = :tf
                      AND time >= :start AND time <= :end
                    """
                ),
                {"symbol": symbol, "tf": timeframe, "start": start, "end": end},
            )
        ).one()
    bars = int(row[0] or 0)
    distinct = int(row[1] or 0)
    span = 0
    if row[2] is not None and row[3] is not None:
        span = int((row[3] - row[2]).total_seconds() // step) + 1
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": bars,
        "distinct_times": distinct,
        "duplicates": bars - distinct,
        "start": row[2].isoformat() if row[2] else None,
        "end": row[3].isoformat() if row[3] else None,
        "expected_if_continuous": span,
        "missing_bars": max(0, span - distinct),
    }


def _label_loaded(symbol: str, timeframe: str, candles: list) -> dict:
    if not candles:
        return {"symbol": symbol, "timeframe": timeframe, "bars": 0}
    signal_config = SignalConfig()
    cfg = signal_config.swing_for(timeframe)
    as_of = datetime.now(timezone.utc)
    quality = audit_candles(candles, timeframe, as_of=as_of)
    usable = candles[:-1] if quality["unfinished_last_bar"] else list(candles)
    swings = detect_swings(
        usable,
        left=cfg.swing_left_bars,
        right=cfg.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=signal_config.atr_period,
        minimum_swing_distance_atr=cfg.minimum_swing_distance_atr,
        as_of_index=len(usable) - 1,
    )
    labels = label_structure_bars(
        usable,
        swings,
        right_bars=cfg.swing_right_bars,
        timeframe=timeframe,
    )
    counts = Counter(row["state"] for row in labels)
    reasons = Counter(row["reason"] for row in labels if row["state"] != "insufficient_data")
    bullish_ages = [
        row["state_age"] for row in labels if row["state"] == "bullish"
    ]
    changes = []
    for index in range(1, len(labels)):
        if labels[index]["state"] != labels[index - 1]["state"] and len(changes) < 6:
            changes.append(
                {
                    "time": str(usable[index].get("time") or usable[index].get("open_time")),
                    "from": labels[index - 1]["state"],
                    "to": labels[index]["state"],
                    "reason": labels[index]["reason"],
                    "event": labels[index]["last_event"],
                    "support": (labels[index]["supporting_swing"] or {}).get("price"),
                    "resist": (labels[index]["resisting_swing"] or {}).get("price"),
                }
            )
    total = len(labels) or 1
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "loaded_bars": len(candles),
        "usable_bars": len(usable),
        "warmup_note": "Every bar is labeled. Early bars are insufficient_data, not dropped.",
        "swing_left": cfg.swing_left_bars,
        "swing_right": cfg.swing_right_bars,
        "minimum_swing_distance_atr": cfg.minimum_swing_distance_atr,
        "confirmed_swings": len(swings),
        "quality": quality,
        "shares": {name: round(100.0 * counts[name] / total, 1) for name in ("bullish", "bearish", "range", "transition", "insufficient_data")},
        "counts": dict(counts),
        "top_reasons": reasons.most_common(4),
        "bullish_state_age_max": max(bullish_ages) if bullish_ages else 0,
        "changes": changes,
    }


async def main() -> None:
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        symbols = await _symbols()
        async with db_manager.engine.begin() as conn:
            end = (
                await conn.execute(
                    text("SELECT max(time) FROM ohlcv WHERE symbol = 'BTCUSDT' AND timeframe = '15m'")
                )
            ).scalar()
        start = end - timedelta(days=60)
        print("window", start.isoformat(), "to", end.isoformat(), "symbols", len(symbols), flush=True)
        quality_rows = []
        for symbol, _group in symbols:
            for timeframe in TIMEFRAMES:
                quality_rows.append(await _window_quality(symbol, timeframe, start, end))
        print("QUALITY", json.dumps(quality_rows), flush=True)
        reports = []
        for symbol in FOCUS:
            for timeframe in TIMEFRAMES:
                candles, _, _ = await _load_research_candles(
                    symbol, timeframe, limit=LIMITS[timeframe], use_research_cache=False
                )
                report = _label_loaded(symbol, timeframe, candles)
                reports.append(report)
                print(
                    symbol,
                    timeframe,
                    "usable",
                    report.get("usable_bars"),
                    "shares",
                    report.get("shares"),
                    "reasons",
                    report.get("top_reasons"),
                    flush=True,
                )
        print("CHANGES", json.dumps([{k: row[k] for k in ("symbol", "timeframe", "changes", "bullish_state_age_max", "confirmed_swings")} for row in reports]), flush=True)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
