"""Orchestrate parity replay + report writing (research-only)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.live_backtest_parity.constants import (
    DEFAULT_SYMBOLS,
    DISCLAIMER,
    SETUP_TIMEFRAME,
)
from app.research.live_backtest_parity.replay import replay_universe
from app.research.live_backtest_parity.report import write_parity_artifacts


def default_output_dir() -> Path:
    # backend/scripts/live_backtest_parity/
    return Path(__file__).resolve().parents[3] / "scripts" / "live_backtest_parity"


def synthetic_trending_series(
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
    wave: float = 4.0,
    start: datetime | None = None,
) -> list[dict[str, Any]]:
    """TEST/fallback OHLCV — marked synthetic; not for production claims."""
    from datetime import timedelta

    t0 = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    out: list[dict[str, Any]] = []
    price = start_price
    bull = drift >= 0
    step = min(abs(drift) if abs(drift) > 1e-9 else 0.5, wave * 0.25)
    for i in range(n):
        t = t0 + timedelta(minutes=i * minutes_per_bar)
        phase = i % 8
        if phase == 0:
            o = c = price
            if bull:
                h, l = price + 0.2, price - wave
            else:
                h, l = price + wave, price - 0.2
        elif phase == 4:
            o = c = price
            if bull:
                h, l = price + wave, price - 0.2
            else:
                h, l = price + 0.2, price - wave
        else:
            o = price
            c = price + (0.25 if bull else -0.25)
            h = max(o, c) + 0.3
            l = min(o, c) - 0.3
        out.append(
            {
                "time": t,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 2000.0,
                "synthetic": True,
            }
        )
        price = price + (step if bull else -step)
    return out


def build_synthetic_universe(
    symbols: Sequence[str] | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    starts = {
        "BTCUSDT": 40_000.0,
        "ETHUSDT": 2_200.0,
        "SOLUSDT": 140.0,
    }
    drifts = {
        "BTCUSDT": 12.0,
        "ETHUSDT": 0.8,
        "SOLUSDT": 0.15,
    }
    waves = {
        "BTCUSDT": 80.0,
        "ETHUSDT": 8.0,
        "SOLUSDT": 1.5,
    }
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for sym in [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]:
        m15 = synthetic_trending_series(
            n=400,
            minutes_per_bar=15,
            start_price=starts.get(sym, 100.0),
            drift=drifts.get(sym, 0.5),
            wave=waves.get(sym, 4.0),
        )
        h1 = synthetic_trending_series(
            n=200,
            minutes_per_bar=60,
            start_price=starts.get(sym, 100.0) * 0.98,
            drift=drifts.get(sym, 0.5) * 3,
            wave=waves.get(sym, 4.0) * 2,
        )
        h4 = synthetic_trending_series(
            n=160,
            minutes_per_bar=240,
            start_price=starts.get(sym, 100.0) * 0.95,
            drift=drifts.get(sym, 0.5) * 8,
            wave=waves.get(sym, 4.0) * 4,
        )
        # Align HTF tip times to setup tip.
        end = m15[-1]["time"]
        for series in (h1, h4):
            shift = end - series[-1]["time"]
            for c in series:
                c["time"] = c["time"] + shift
        out[sym] = {"15m": m15, "1h": h1, "4h": h4}
    return out


async def load_universe_from_db(
    symbols: Sequence[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    from app.research.postgres_ohlcv import load_ohlcv_series_range

    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for sym in [s.upper() for s in symbols]:
        pack: dict[str, list[dict[str, Any]]] = {}
        for tf in ("15m", "1h", "4h"):
            candles = await load_ohlcv_series_range(
                sym,
                tf,
                start=start,
                end_exclusive=end,
                warmup_bars=80,
            )
            pack[tf] = candles
        if pack["15m"] and pack["1h"] and pack["4h"]:
            out[sym] = pack
    return out


async def run_parity_validation(
    *,
    symbols: Sequence[str] | None = None,
    out_dir: Path | str | None = None,
    use_db: bool = True,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe: str = SETUP_TIMEFRAME,
    max_bars: int | None = None,
    series_by_symbol: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]]
    | None = None,
) -> dict[str, Any]:
    """Run replay + write artifacts under scripts/live_backtest_parity/."""
    syms = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
    out = Path(out_dir) if out_dir else default_output_dir()
    data_source = "provided"
    universe: dict[str, Any]

    if series_by_symbol is not None:
        universe = dict(series_by_symbol)
        data_source = "provided"
    elif use_db:
        try:
            universe = await load_universe_from_db(syms, start=start, end=end)
            data_source = "postgres_ohlcv"
            if not universe:
                universe = build_synthetic_universe(syms)
                data_source = "synthetic_fallback_empty_db"
        except Exception as exc:  # noqa: BLE001
            universe = build_synthetic_universe(syms)
            data_source = f"synthetic_fallback:{type(exc).__name__}"
    else:
        universe = build_synthetic_universe(syms)
        data_source = "synthetic"

    result = replay_universe(
        series_by_symbol=universe,
        symbols=syms,
        timeframe=timeframe,
        max_bars=max_bars,
    )
    summary = write_parity_artifacts(
        out,
        events=result["events"],
        mismatches=result["mismatches"],
        future_data_fails=result["future_data_fails"],
        telegram_stats=result["telegram_stats"],
        per_symbol=result["per_symbol"],
        extra={
            "data_source": data_source,
            "disclaimer": DISCLAIMER,
            "timeframe": timeframe,
            "symbols": syms,
        },
    )
    summary["output_dir"] = str(out)
    summary["data_source"] = data_source
    return summary


def run_parity_validation_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_parity_validation(**kwargs))
