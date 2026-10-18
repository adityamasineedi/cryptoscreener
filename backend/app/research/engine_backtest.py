"""Backtest-screen adapter for the ranked strategies outside the COMBO family.

CANDLE12_V2, the multi-cap strategies and the OHLCV structure experiment each
have their own engine. This module calls those engines as they are, the same
way ``scripts/strategy_rank/multicap/run_multicap_rank.py`` does, and shapes
the result as a Strategy Backtest row. It does not change a signal, a stop, a
target, a fill or a fee rule. Research only: no paper or live path.
"""

from __future__ import annotations

import asyncio
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_OSE_VARIANTS = ("A_corrected_baseline", "B_acceptance", "C_retest", "D_faster_confirmation")
_MULTICAP_MODULES = {
    "LARGE_CAP_SWEEP_CHOCH": "large_cap_sweep_choch",
    "MID_CAP_FVG_DISCOUNT": "mid_cap_fvg_discount",
    "SMALL_CAP_VOLUME_BOS": "small_cap_volume_bos",
}

_EXEC_MULTICAP = (
    "MARKET entry at signal-bar close; exits from next bar; ATR(14)x1.5 or structural "
    "stop (farther), TP1 at 2R (first target touched exits full size); same-bar SL+TP "
    "-> SL (CONSERVATIVE); one open trade at a time; taker both sides on executed "
    "notional; no slippage in USD"
)
_EXEC_CANDLE12 = (
    "Candle-1 BOS setup, Candle-2 stop-entry at C1 high/low trigger with 0.02% adverse "
    "entry slippage; structural stop, production targets; exit fill with 0.02% adverse "
    "slippage (engine _exit_fill); same-bar SL+TP -> SL; one open trade at a time across "
    "both directions; taker both sides"
)
_EXEC_CANDLE12_V3 = (
    "CANDLE12_V3_EXEC_FIX: CANDLE12_V2 Candle-1 setup, BOS once per broken level; Candle-2 "
    "stop entry, fill = C1 high/low with 0.02% adverse slippage (inclusive), or C2 open with "
    "slippage when C2 opens beyond the trigger; structural stop; full exit at the nearest "
    "target (nearest_target_full_exit); a bar opening beyond the stop exits at its open, "
    "beyond a target at the target; same-bar SL+TP -> SL; one open trade per side (LONG-only "
    "or SHORT-only book); qty floored to the exchange step, min qty enforced; taker both "
    "sides; no funding"
)
_EXEC_OSE = (
    "first close through a confirmed swing; entry next bar open with 0.02% adverse "
    "slippage; stop = opposite confirmed swing; target 2R; 20-bar hold then close exit "
    "with slippage; gap-through-stop exits at open; same-bar -> stop; one position per "
    "symbol; taker both sides"
)
_EXEC_DAILY_TREND = (
    "Pre-registered daily trend book {book} (research only, in-sample 2022-2026): signal on "
    "closed daily bar, fill next open; stop 2.5xATR(14) from the fill; gap through stop fills "
    "at open; stop before indicator exits; one position; no same-bar reversal; open position "
    "closed at the last bar; adverse slippage 0.05% BTC/ETH, 0.15% others; taker both sides; "
    "fixed-$ risk sizing (screen compounds 0.5%); NO funding (funding_rates has no rows before "
    "2026-10)"
)

# Daily trend books (app.research.daily_trend.books.TAB_BOOKS): id, book, warm-up bars.
# Names repeat books.BOOK_NAMES so importing this module does not import pandas.
# Warm-up bars: >= 400 so the recursive ATR(14)/ADX(14) used by every book settles and book 19's
# realized-vol rule (20 + 365 bars) is known; EMA200 needs ~1000 bars for its seed to fade.
# Postgres 1d starts 2022-10, so early windows get whatever history exists.
_DAILY_TREND_BOOKS: tuple[tuple[str, int, int, str], ...] = (
    ("DAILY_TREND_01_SMA200", 1, 400, "SMA200"),
    ("DAILY_TREND_02_EMA200", 2, 1000, "EMA200"),
    ("DAILY_TREND_03_SMA50", 3, 400, "SMA50"),
    ("DAILY_TREND_04_MA_SLOPE", 4, 400, "MA slope"),
    ("DAILY_TREND_05_GOLDEN_CROSS", 5, 400, "Golden/death cross"),
    ("DAILY_TREND_06_WEINSTEIN", 6, 400, "Weinstein"),
    ("DAILY_TREND_09_DONCHIAN20_WEINSTEIN", 9, 400, "Donchian 20 gated by Weinstein"),
    ("DAILY_TREND_11_ADX_DMI", 11, 400, "ADX/DMI 14"),
    ("DAILY_TREND_12_ICHIMOKU", 12, 400, "Ichimoku 9/26/52"),
    ("DAILY_TREND_14_ROC90", 14, 400, "ROC 90"),
    ("DAILY_TREND_15_ROC30", 15, 400, "ROC 30"),
    ("DAILY_TREND_19_COMBINED", 19, 400, "Combined (4 side, 7 trigger)"),
)
DAILY_TREND_FAMILY = "daily_trend"
# The pre-registered screen's holdout: no bar at or after this instant may be loaded.
DAILY_TREND_HOLDOUT_START = datetime(2026, 7, 15, tzinfo=timezone.utc)
DAILY_TREND_LAST_END_DATE = "2026-07-14"  # inclusive UI end date (end_exclusive 2026-07-15)
DAILY_TREND_HOLDOUT_CODE = "daily_trend_holdout_sealed"
DAILY_TREND_HOLDOUT_MESSAGE = (
    "daily trend books: holdout from 2026-07-15 is sealed; end date must be <= 2026-07-14"
)
_DAILY_TREND_SLIP_LOW = {"BTCUSDT": 0.0005, "ETHUSDT": 0.0005}
_DAILY_TREND_SLIP_OTHER = 0.0015

# 4h book 16 from the pre-registered 4h/1h screen (v2 dual-source run, 2026-10-18): the only
# COMPLETE + SCREEN_HIT evaluations were book 16 on BTCUSDT and ETHUSDT. Same family as the daily
# books (same rule code in bar counts, same sealed holdout and guards), but 4h bars, those two coins,
# LONG only, and funding charged from the bundled settlements (app.research.daily_trend.funding).
# Warm-up 1000 bars (~167 days): book 16's 252-bar high and SMA200 are exact windows; ATR(14) settles.
_EXEC_TREND_4H = (
    "Pre-registered 4h/1h screen book {book} (research only, in-sample 2022-2026; COMPLETE + "
    "SCREEN_HIT on BTCUSDT and ETHUSDT only): signal on closed 4h bar, fill next open; stop "
    "2.5xATR(14) from the fill; gap through stop fills at open; stop before indicator exits; one "
    "position; open position closed at the last bar; adverse slippage 0.05%; taker both sides; "
    "fixed-$ risk sizing (screen compounds 0.5%); funding charged from bundled Binance settlements "
    "through 2026-07-14 (July 2026 fapi rows have no documented schedule: funding UNRESOLVED)"
)
_TREND_4H_BOOKS: tuple[tuple[str, int, int, str], ...] = (
    ("MTF_4H_16_BREAKOUT252", 16, 1000, "252-bar closing-high breakout"),
)
_TREND_4H_SYMBOLS = ("BTCUSDT", "ETHUSDT")

# Warm-up bars are the ones the ranking run asked for, per engine.
ENGINE_STRATEGIES: dict[str, dict[str, Any]] = {
    "CANDLE12_V2": {
        "family": "candle12",
        "name": "CANDLE12 V2",
        "directions": ("LONG", "SHORT"),
        "timeframes": ("5m", "15m", "1h"),
        "warmup_bars": 1000,
        "execution_model": _EXEC_CANDLE12,
    },
    "CANDLE12_V3_EXEC_FIX": {
        "family": "candle12_v3",
        "name": "CANDLE12 V3 EXEC FIX",
        "directions": ("LONG", "SHORT"),
        "timeframes": ("5m", "15m", "1h"),
        "warmup_bars": 1000,
        "execution_model": _EXEC_CANDLE12_V3,
    },
    **{
        sid: {
            "family": "multicap",
            "name": sid.replace("_", " "),
            "directions": ("LONG",),
            "timeframes": ("5m", "15m", "1h"),
            "warmup_bars": 1000,
            "execution_model": _EXEC_MULTICAP,
        }
        for sid in _MULTICAP_MODULES
    },
    **{
        f"OSE_{variant}": {
            "family": "ohlcv_structure_experiment",
            "name": f"OSE {variant.replace('_', ' ')}",
            "variant": variant,
            "directions": ("LONG", "SHORT"),
            "timeframes": ("15m", "1h"),
            "warmup_bars": 500,
            "execution_model": _EXEC_OSE,
        }
        for variant in _OSE_VARIANTS
    },
    **{
        sid: {
            "family": DAILY_TREND_FAMILY,
            "name": f"Daily trend {book:02d} {label}",
            "book": book,
            # The book always walks long+short (the screen's path); the row keeps a side.
            "directions": ("LONG", "SHORT", "BOTH"),
            "timeframes": ("1d",),
            "warmup_bars": warmup,
            "execution_model": _EXEC_DAILY_TREND.format(book=book),
        }
        for sid, book, warmup, label in _DAILY_TREND_BOOKS
    },
    **{
        sid: {
            "family": DAILY_TREND_FAMILY,
            "name": name,
            "book": book,
            "directions": ("LONG", "SHORT", "BOTH"),
            "timeframes": ("1d",),
            # books' warm-up (EMA200 1000) plus the 6-month score lookback
            "warmup_bars": 1200,
            "execution_model": model,
        }
        for sid, book, name, model in (
            ("DAILY_TREND_ROTATION", "rotation", "Daily trend rotation (top 3 of 12, 6m, monthly)",
             "Walk-forward rotation, pre-registered in docs/daily_trend_rotation_prereg.md (sha256 24da2ebd...): "
             "on each month start, score the 12 books by trailing 6-month net R of shadow trades that exited "
             "before that day; trade the top 3 with a positive score at 1/3 risk each for entries that month; "
             "flat if none. Book execution as the daily trend books. Overlapping sleeves on one coin are allowed. "
             "Research only, in-sample diagnostic; no funding."),
            ("DAILY_TREND_STATIC12", "static12", "Daily trend static 12 (benchmark, 1/12 risk each)",
             "Benchmark for the rotation: all 12 books' shadow trades at 1/12 risk each, no selection. Book "
             "execution as the daily trend books. Research only, in-sample diagnostic; no funding."),
        )
    },
    **{
        sid: {
            "family": DAILY_TREND_FAMILY,
            "name": f"4h trend {book} {label}",
            "book": book,
            "directions": ("LONG",),
            "timeframes": ("4h",),
            "symbols": _TREND_4H_SYMBOLS,
            "bar_hours": 4,
            "funding": True,
            "warmup_bars": warmup,
            "execution_model": _EXEC_TREND_4H.format(book=book),
        }
        for sid, book, warmup, label in _TREND_4H_BOOKS
    },
}
_BY_UPPER = {sid.upper(): sid for sid in ENGINE_STRATEGIES}


def _gross_profit_factor(trades: Sequence[Mapping[str, Any]]) -> float | None:
    gross = [float(t["gross_pnl_usd"]) for t in trades if t.get("gross_pnl_usd") is not None]
    wins = sum(p for p in gross if p > 0)
    losses = -sum(p for p in gross if p < 0)
    return (wins / losses) if losses > 0 else None


def engine_strategy_id(value: str | None) -> str | None:
    """Canonical id for a ranked engine strategy, or None for a COMBO id."""
    return _BY_UPPER.get(str(value or "").strip().upper())


def is_daily_trend(strategy_id: str | None) -> bool:
    sid = engine_strategy_id(strategy_id)
    return sid is not None and ENGINE_STRATEGIES[sid]["family"] == DAILY_TREND_FAMILY


class DailyTrendHoldoutError(ValueError):
    """A daily trend request would read the sealed holdout (2026-07-15 onward)."""

    code = DAILY_TREND_HOLDOUT_CODE


def daily_trend_dates(
    start_date: str | None, end_date: str | None, limit: int, *, bar_hours: int = 24
) -> tuple[str | None, str]:
    """(start_date, end_date) for a daily trend load that never reaches the holdout.

    Dates are UI calendar days; end_date is inclusive (the loader reads up to
    end_date + 1 day exclusive). An end after 2026-07-14 is rejected. Without an
    end the window ends 2026-07-14; without any date (tail mode) it is the days
    holding ``limit`` bars of ``bar_hours`` ending 2026-07-14, so the tail loader
    is never used.
    """
    from datetime import timedelta

    from app.research.query_utils import parse_utc_calendar_day

    label = "daily trend books" if bar_hours == 24 else f"{bar_hours}h trend book"
    message = DAILY_TREND_HOLDOUT_MESSAGE.replace("daily trend books", label)
    last = parse_utc_calendar_day(DAILY_TREND_LAST_END_DATE)
    try:
        start_day = parse_utc_calendar_day(start_date)
        end_day = parse_utc_calendar_day(end_date)
    except ValueError as exc:
        raise DailyTrendHoldoutError(f"{label}: unreadable date ({exc})") from exc
    if end_day is not None and end_day > last:
        raise DailyTrendHoldoutError(message)
    if start_day is not None and start_day > last:
        raise DailyTrendHoldoutError(message + f"; start date {start_day.isoformat()} is inside it")
    if end_day is None:
        end_day = last
        if start_day is None:
            days = -(-max(1, int(limit)) * int(bar_hours) // 24)
            start_day = last + timedelta(days=1) - timedelta(days=days)
    return (start_day.isoformat() if start_day else None), end_day.isoformat()


def engine_request_error(
    strategy_id: str, direction: str, timeframes: Sequence[str], symbols: Sequence[str] = ()
) -> str | None:
    """Why this engine cannot run the request, or None when it can."""
    spec = ENGINE_STRATEGIES[strategy_id]
    if direction.upper() not in spec["directions"]:
        return f"{strategy_id} runs {' or '.join(spec['directions'])} only"
    allowed = spec.get("symbols")
    off = [s for s in symbols if allowed is not None and str(s).upper() not in allowed]
    if off:
        return f"{strategy_id} runs {', '.join(allowed)} only; got {', '.join(off)}"
    bad = [tf for tf in timeframes if tf not in spec["timeframes"]]
    if bad:
        return (
            f"{strategy_id} supports timeframes {', '.join(spec['timeframes'])}; "
            f"got {', '.join(bad)}"
        )
    return None


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(value)


def _run_multicap(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    import importlib

    from app.research.combination_backtest import evaluate_candidate_trades
    from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter
    from app.research.multi_cap_strategies.cap_filter import symbol_eligible_for_strategy
    from app.research.multi_cap_strategies.config import MultiCapResearchConfig
    from app.research.trade_fees import enrich_trades

    sid = str(data["strategy_id"])
    mod = importlib.import_module(f"app.research.multi_cap_strategies.{_MULTICAP_MODULES[sid]}")
    cfg = MultiCapResearchConfig()  # engine defaults untouched (signal + SL/TP params)
    definition = mod.DEFINITION
    candles = list(data["candles"])
    try:
        _ok, label, _why = symbol_eligible_for_strategy(
            str(data["symbol"]), data.get("market_cap"), definition.asset_group, config=cfg
        )
    except Exception:  # noqa: BLE001
        label = definition.asset_group
    cands = mod.generate_candidates(
        str(data["symbol"]),
        str(data["timeframe"]),
        candles,
        config=cfg,
        index_start=int(data.get("index_start") or 0),
    )
    trades = MultiCapStrategyAdapter(cfg).candidates_to_trades(
        cands, asset_group=label, period_label="FULL"
    )
    evaluated = evaluate_candidate_trades(
        candles,
        trades,
        handling=cfg.ambiguous_handling,
        one_open_at_a_time=cfg.one_open_at_a_time,
    )
    closed = [t.to_dict() for t in evaluated if t.outcome and t.outcome != "OPEN"]
    opened = sum(1 for t in evaluated if t.outcome == "OPEN")
    enriched = enrich_trades(closed, **sizing)
    for t in enriched:
        t["entry_time"] = t.get("signal_time")
    return enriched, opened


def _run_candle12(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research import candle12_v2 as c12
    from app.research.trade_fees import enrich_trade_execution

    direction = str(data["direction"]).upper()
    res = c12.run_candle12_v2(
        str(data["symbol"]),
        str(data["timeframe"]),
        list(data["candles"]),
        index_start=int(data.get("index_start") or 0),
        progress_every=0,
    )
    if res.get("status") not in (None, "OK") and not res.get("trades"):
        raise RuntimeError(f"candle12_v2 engine status {res.get('status')}")
    v2 = (
        c12.Candle12V2Config(
            **{k: res["v2_config"][k] for k in ("trading_fee", "entry_slippage", "exit_slippage")}
        )
        if res.get("v2_config")
        else None
    )
    # One both-direction walk (shared position lock); this row keeps one side.
    side = [t for t in res.get("trades") or [] if t.get("direction") == direction]
    closed = [
        t for t in side if t.get("outcome") not in (None, "OPEN") and t.get("exit_price") is not None
    ]
    enriched: list[dict[str, Any]] = []
    for t in closed:
        fill = c12._exit_fill(direction, float(t["exit_price"]), v2)
        snap = dict(t.get("condition_snapshot") or {})
        e = enrich_trade_execution(
            {
                **t,
                "exit_price": fill,
                "raw_exit_price": t["exit_price"],
                "entry_time": t.get("signal_time"),
                "condition_snapshot": {"entry_type": snap.get("entry_type") or "MARKET"},
            },
            **sizing,
        )
        e["engine_net_R"] = t.get("r_multiple")
        enriched.append(e)
    return enriched, len(side) - len(closed)


def _run_candle12_v3(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research.candle12_v3 import Candle12V3Config, run_candle12_v3

    # The row's direction is the whole book: per-side lock, no hidden other side.
    cfg = Candle12V3Config(
        book_side=str(data["direction"]).upper(),
        taker_fee=sizing["taker_fee"],
        risk_usd=sizing["risk_usd"],
        account_equity=sizing["account_equity"],
        leverage=sizing["leverage"],
    )
    res = run_candle12_v3(
        str(data["symbol"]),
        str(data["timeframe"]),
        list(data["candles"]),
        config=cfg,
        index_start=int(data.get("index_start") or 0),
    )
    trades = res["trades"]
    closed = [t for t in trades if t["outcome"] != "OPEN"]
    return closed, len(trades) - len(closed)


def _run_ose(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    from app.research.ohlcv_structure_experiment.engine import breaks_for_variant, run_variant
    from app.research.trade_fees import enrich_trade_execution

    direction = str(data["direction"]).upper()
    variant = ENGINE_STRATEGIES[str(data["strategy_id"])]["variant"]
    candles = list(data["candles"])
    first = int(data.get("index_start") or 0)
    events = breaks_for_variant(candles, symbol=str(data["symbol"]), variant=variant)
    events = [e for e in events if e.break_index >= first]
    records = run_variant(candles, events, variant)
    closed: list[dict[str, Any]] = []
    opened = 0
    for r in records:
        if str(r.event.direction).upper()[:1] != direction[:1]:
            continue
        if r.status == "entry_qualified":
            opened += 1  # qualified but window incomplete -> never filled
            continue
        if r.status != "filled" or r.exit_price is None or r.entry_price is None:
            continue
        entry_time = _iso(candles[r.entry_index]["time"])
        # The decision bar is the closed bar that qualified the setup (the break bar
        # for variants A and D); the fill is the next bar's open.
        decision_index = r.qualified_index if r.qualified_index is not None else r.entry_index - 1
        signal_time = _iso(candles[decision_index]["time"])
        closed.append(
            enrich_trade_execution(
                {
                    "direction": direction,
                    "entry_price": r.entry_price,
                    "stop_price": r.stop_price,
                    "exit_price": r.exit_price,
                    "outcome": r.outcome,
                    "entry_type": "MARKET",
                    "signal_time": signal_time,
                    "entry_time": entry_time,
                    "fill_time": entry_time,
                    "exit_time": _iso(candles[r.exit_index]["time"]),
                    "entry_index": r.entry_index,
                    "exit_index": r.exit_index,
                    # The engine's own count: entry bar through exit bar inclusive
                    # (a HOLD_BARS timeout holds HOLD_BARS).
                    "holding_bars": r.holding_bars,
                    "engine_net_r": r.net_r,
                    "event_id": r.event.event_id,
                    "target_price": r.target_price,
                    "tp1": r.target_price,
                    "ambiguous": r.ambiguous,
                },
                **sizing,
            )
        )
    return closed, opened


_DAILY_TREND_SIDES = {"LONG": (1,), "SHORT": (-1,), "BOTH": (1, -1)}
_DAILY_TREND_OUTCOME = {
    "stop": "SL",
    "stop_gap": "SL",
    "signal": "SIGNAL_EXIT",
    "window_end": "WINDOW_END",
}


def _run_daily_trend(data: Mapping[str, Any], sizing: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Daily trend book: the locked books.simulate_path walk, sized by trade_fees.

    simulate_path's entry_price / exit_price already include the adverse slippage,
    so they go to enrich_trade_execution as the fills (slippage_rate 0, as for
    CANDLE12 / OSE): qty = risk $ / |fill - stop| = risk $ / 2.5 ATR, the screen's
    sizing distance. raw_* keep the unslipped open / stop / close.

    The 4h trend book walks the same code on 4h bars (rules count bars) and adds
    funding per trade (daily_trend.funding): net_pnl / net_pnl_usd / r_net include
    it; *_before_funding keep the trade_fees values (VectorBT replays those).
    """
    from datetime import timedelta

    import numpy as np
    import pandas as pd

    from app.research.daily_trend.books import RISK, build_books, simulate_path
    from app.research.daily_trend.rotation import rotation_items, static_items
    from app.research.query_utils import candle_timestamp
    from app.research.trade_fees import enrich_trade_execution

    sid = str(data["strategy_id"])
    spec = ENGINE_STRATEGIES[sid]
    _b = spec["book"]
    book = _b if isinstance(_b, str) else int(_b)
    direction = str(data["direction"]).upper()
    if direction not in _DAILY_TREND_SIDES or direction not in spec["directions"]:
        raise ValueError(f"{sid}: direction {direction!r} ({', '.join(spec['directions'])})")
    symbol = str(data["symbol"]).upper()
    if spec.get("symbols") is not None and symbol not in spec["symbols"]:
        raise ValueError(f"{sid}: runs {', '.join(spec['symbols'])} only; got {symbol}")
    bar = timedelta(hours=int(spec.get("bar_hours", 24)))
    candles = list(data["candles"])
    times: list[datetime] = []
    for c in candles:
        ts = candle_timestamp(c)
        if ts is None:
            raise ValueError(f"{sid}: candle without a time")
        if ts >= DAILY_TREND_HOLDOUT_START:
            raise RuntimeError(
                f"{sid}: candle {ts.isoformat()} is inside the sealed holdout (from 2026-07-15); "
                "nothing at or after 2026-07-15 may be loaded"
            )
        times.append(ts)
    now = data.get("now_utc") or datetime.now(timezone.utc)
    if times and times[-1] + bar > now:
        times.pop()  # still-forming bar
        candles = candles[: len(times)]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError(f"{sid}: candle times are not strictly increasing")
    # The screen ran on gap-free UTC bars; rolling lookbacks count bars, so a missing bar would
    # silently change every indicator. Refuse the cell instead.
    gaps = [(a, b) for a, b in zip(times, times[1:]) if b - a != bar]
    if gaps:
        a, b = gaps[0]
        what = "daily" if bar == timedelta(days=1) else f"{bar.total_seconds() / 3600:g}h"
        first = (f"{a.date()} and {b.date()}" if bar == timedelta(days=1)
                 else f"{a.isoformat()} and {b.isoformat()}")
        raise ValueError(f"{sid}: missing {what} bars ({len(gaps)} gap(s); first between {first})")
    i_start = int(data.get("index_start") or 0)
    i_end = len(times) - 1
    if i_end < 1 or i_start > i_end:
        return [], 0

    idx = pd.DatetimeIndex(times).tz_convert("UTC").as_unit("ns")
    d = pd.DataFrame(
        {k: np.asarray([float(c[k]) for c in candles], dtype=float) for k in ("open", "high", "low", "close")},
        index=idx,
    )
    slip = _DAILY_TREND_SLIP_LOW.get(symbol, _DAILY_TREND_SLIP_OTHER)
    books = build_books(d)
    fs = None
    if spec.get("funding"):
        from app.research.daily_trend.funding import FundingSchedule, bundled_funding

        fs = FundingSchedule(bundled_funding(symbol), idx, d["open"].to_numpy())
        t_ns = idx.asi8
        bar_ns = int(bar.total_seconds() * 1e9)
    if book == "rotation":
        items, _sched = rotation_items(d, slip, float(sizing["taker_fee"]), i_start, i_end, books)
    elif book == "static12":
        items = static_items(d, slip, i_start, i_end, books)
    else:
        items = [(book, p, 1.0) for p in simulate_path(d, book, i_start, i_end, slip, books)]
    o, c_ = d["open"].to_numpy(), d["close"].to_numpy()
    keep = _DAILY_TREND_SIDES[direction]

    def _signal_exit_index(b: int, side: int, entry_index: int) -> int | None:
        # The indicator exit does not depend on the stop: first close t >= entry with
        # state != side fills at open t + 1 (none at i_end, where window_end closes).
        state = books[b]["state"]
        for t in range(entry_index, i_end):
            if state[t] != side:
                return t + 1
        return None

    out: list[dict[str, Any]] = []
    for b, p, scale in items:
        risk_known = books[b].get("risk_rule_known")
        side = int(p["side"])
        if side not in keep:
            continue
        reason = str(p["exit_reason"])
        ei, xi, si = int(p["entry_index"]), int(p["exit_index"]), int(p["signal_index"])
        raw_exit = (
            float(p["stop_price"])
            if reason == "stop"
            else float(c_[xi]) if reason == "window_end" else float(o[xi])
        )
        sx = _signal_exit_index(b, side, ei)
        risk_frac = float(p["risk_frac"])
        trade = enrich_trade_execution(
            {
                "direction": "LONG" if side == 1 else "SHORT",
                "entry_price": float(p["entry_price"]),
                "stop_price": float(p["stop_price"]),
                "exit_price": float(p["exit_price"]),
                "raw_entry_price": float(o[ei]),
                "raw_exit_price": raw_exit,
                "outcome": _DAILY_TREND_OUTCOME[reason],
                "exit_reason": reason,
                "entry_type": "MARKET",
                "signal_time": times[si],
                "entry_time": times[ei],
                "fill_time": times[ei],
                "exit_time": times[xi],
                "signal_index": si,
                "entry_index": ei,
                "exit_index": xi,
                # Entry bar through exit bar inclusive (the OSE convention).
                "holding_bars": xi - ei + 1,
                "book": b,
                "sleeve": book if book in ("rotation", "static12") else None,
                "risk_scale": scale,
                "atr": float(p["atr"]),
                "risk_frac": risk_frac,
                "risk_rule_known": (bool(risk_known[si]) if risk_known is not None else None),
                "slippage_in_fills": slip,
                "signal_exit_time": _iso(times[sx]) if sx is not None else None,
                "window_end_time": _iso(times[i_end]),
                "tp1": None,
            },
            **{**sizing, "risk_usd": float(sizing["risk_usd"]) * risk_frac / RISK * scale},
        )
        if fs is not None:
            # The screen's ledger bounds: through the exit open (stop_gap / signal, and the
            # open of an intrabar stop bar) or through the last bar's close (window_end).
            bound = t_ns[xi] + bar_ns if reason == "window_end" else t_ns[xi]
            stop_bar = (int(t_ns[xi]), int(t_ns[xi]) + bar_ns) if reason == "stop" else None
            f = fs.trade(side, float(trade["qty"]), int(t_ns[ei]), int(bound), stop_bar)
            risk = float(trade["risk_usd"])
            net = float(trade["net_pnl_usd"]) + f["funding_usd"]
            trade = {
                **trade,
                **f,
                "net_pnl_before_funding_usd": trade["net_pnl_usd"],
                "r_net_before_funding": trade["r_net"],
                "net_pnl": net,
                "net_pnl_usd": net,
                "r_net": (net / risk) if risk > 0 else trade["r_net"],
            }
        out.append(trade)
    return out, 0


_RUNNERS: dict[str, Callable[[Mapping[str, Any], dict[str, Any]], tuple[list[dict[str, Any]], int]]] = {
    "multicap": _run_multicap,
    "candle12": _run_candle12,
    "candle12_v3": _run_candle12_v3,
    "ohlcv_structure_experiment": _run_ose,
    DAILY_TREND_FAMILY: _run_daily_trend,
}


def run_engine_cell(data: Mapping[str, Any]) -> dict[str, Any]:
    """Run one strategy/direction/symbol/timeframe cell. Pure: candles in, trades out.

    Sizing matches the ranking run: fixed risk, leverage cap against the constant
    account equity, taker and maker rates from the request.
    """
    t0 = time.perf_counter()
    sid = str(data["strategy_id"])
    spec = ENGINE_STRATEGIES[sid]
    sizing = {
        "risk_usd": float(data["risk_usd"]),
        "taker_fee": float(data["taker_fee"]),
        "maker_fee": float(data["maker_fee"]),
        "leverage": float(data["leverage"]),
        "account_equity": float(data["principal"]),
    }
    trades, opened = _RUNNERS[spec["family"]](data, sizing)
    trades = [t for t in trades if t.get("net_pnl_usd") is not None]
    trades.sort(key=lambda t: (str(t.get("exit_time") or ""), str(t.get("entry_time") or "")))
    for i, t in enumerate(trades, 1):
        t["trade_no"] = i
        if t.get("trade_id") is None:
            t["trade_id"] = f"t{i}"
        for key in ("signal_time", "entry_time", "fill_time", "exit_time"):
            if key in t:
                t[key] = _iso(t[key])
    try:
        from app.research.trade_context import attach_trade_context

        attach_trade_context(
            trades,
            list(data["candles"]),
            timeframe=str(data["timeframe"]),
            symbol=str(data["symbol"]),
            # CANDLE12 V3 stamps the entry bar's open, so the bar before it (the closed
            # signal bar) is the decision bar. OSE stamps signal_time on the decision bar.
            decision_offset=-1 if spec["family"] == "candle12_v3" else 0,
        )
    except Exception:  # noqa: BLE001
        pass  # display-only labels never fail a backtest
    return {
        "status": "OK" if trades else "SUCCESS_EMPTY",
        "trades": trades,
        "open_trades_at_end": opened,
        "execution_model": spec["execution_model"],
        "elapsed_seconds": time.perf_counter() - t0,
    }


def build_engine_row(
    out: Mapping[str, Any],
    *,
    strategy_id: str,
    direction: str,
    symbol: str,
    timeframe: str,
    risk_usd: float,
    principal: float,
    leverage: float,
    bars_loaded: int,
    warmup_bars: int,
    candle_source: str | None,
    period_start: str | None,
    period_end: str | None,
    include_trades: bool = True,
) -> dict[str, Any]:
    """Strategy Backtest row with the same keys the COMBO rows carry."""
    from app.research.trade_fees import summarize_net_trades

    trades = list(out.get("trades") or [])
    net = summarize_net_trades(trades, risk_usd=risk_usd)
    nets = [float(t["net_pnl_usd"]) for t in trades]
    r_gross = [float(t["r_gross"]) for t in trades if t.get("r_gross") is not None]
    r_net = [float(t["r_net"]) for t in trades if t.get("r_net") is not None]
    curve_r: list[float] = []
    cum = peak = dd_r = 0.0
    for r in r_net:
        cum += r
        peak = max(peak, cum)
        dd_r = max(dd_r, peak - cum)
        curve_r.append(round(cum, 6))
    n = len(trades)
    # Display counts only. OSE outcomes are "target" / "stop" / "timeout"; a timeout is neither.
    tp_hits = sum(
        1
        for t in trades
        if str(t.get("outcome") or "").upper().startswith("TP")
        or str(t.get("outcome") or "").upper() == "TARGET"
    )
    sl_hits = sum(
        1 for t in trades if str(t.get("outcome") or "").upper() in ("SL", "STOP", "STOP_LOSS")
    )
    return {
        "combination_id": strategy_id,
        "name": ENGINE_STRATEGIES[strategy_id]["name"],
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "structure": "engine-defined",
        "sample_size": n,
        "average_R": (sum(r_gross) / len(r_gross)) if r_gross else None,
        "average_R_net": net["average_R_net"],
        "average_R_usd_basis_gross": net["average_R_usd_basis_gross"],
        "average_R_usd_basis_net": net["average_R_usd_basis_net"],
        "profit_factor_net": net["profit_factor_net"],
        "win_rate_net": net["win_rate_net"],
        "max_drawdown_usd_net": net["max_drawdown_usd_net"],
        "equity_curve_usd_net": net["equity_curve_usd_net"],
        "r_definition": net["r_definition"],
        "expectancy_R": net["average_R_net"],
        "tp1_hit_rate": (tp_hits / n) if n else None,
        "sl_rate": (sl_hits / n) if n else None,
        # Gross PF from each trade's gross $ (before fees), on the same basis as PF net.
        "profit_factor": _gross_profit_factor(trades),
        "max_drawdown_R": dd_r if r_net else None,
        "tp1_hits": tp_hits,
        "sl_hits": sl_hits,
        "period_start": period_start,
        "period_end": period_end,
        "r_values": r_net,
        "equity_curve_r": curve_r,
        "pnl_usd": sum(float(t["gross_pnl_usd"]) for t in trades) if trades else None,
        "pnl_usd_net": sum(nets) if trades else None,
        "fees_usd": sum(float(t.get("fee_total_usd") or 0) for t in trades),
        "risk_usd": risk_usd,
        "principal_usd": principal,
        "leverage": leverage,
        "bars_loaded": bars_loaded,
        "warmup_bars": warmup_bars,
        "bars_evaluated": max(0, bars_loaded - warmup_bars),
        "candle_source": candle_source,
        "trades": trades if include_trades else [],
        "entry_diagnostics": {},
        "open_trades_at_end": int(out.get("open_trades_at_end") or 0),
        "status": out.get("status") or ("OK" if n else "SUCCESS_EMPTY"),
        "execution_model": out.get("execution_model"),
        "engine_family": ENGINE_STRATEGIES[strategy_id]["family"],
        "engine_fill_model": out.get("execution_model"),
        "market_structure": {"analytics_enabled": False},
        **_funding_row_fields(strategy_id, trades),
    }


FUNDING_PNL_LABEL_COMPLETE = "After fees, slippage and funding."
FUNDING_PNL_LABEL_INCOMPLETE = "After fees, slippage and included funding; funding incomplete/unresolved."


def _funding_row_fields(strategy_id: str, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Row funding total, worst coverage and PnL label for strategies that charge funding."""
    if not ENGINE_STRATEGIES[strategy_id].get("funding"):
        return {}
    from app.research.daily_trend.funding import worst_coverage

    status = worst_coverage([dict(t) for t in trades])
    return {
        "funding_total_usd": sum(float(t.get("funding_usd") or 0.0) for t in trades),
        "funding_coverage_status": status,
        # No trades: nothing was charged and nothing is missing.
        "pnl_label": (
            FUNDING_PNL_LABEL_COMPLETE if status in (None, "COMPLETE") else FUNDING_PNL_LABEL_INCOMPLETE
        ),
    }


def _cli(argv: list[str]) -> int:
    """Subprocess entry: pickle payload in, pickle result out."""
    if len(argv) != 3:
        return 2
    os.environ["RESEARCH_CPU_WORKER"] = "1"
    try:
        with Path(argv[1]).open("rb") as f:
            result = run_engine_cell(pickle.load(f))
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
        }
    Path(argv[2]).write_bytes(pickle.dumps(result))
    return 0


async def run_engine_cell_isolated(
    payload: dict[str, Any],
    *,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run ``run_engine_cell`` in its own interpreter so the API loop stays free.

    The engines take no cancel hook, so a cancel stops the child process.
    """
    from app.research.backtest_cpu_pool import _spawn_python_executable, _write_payload_blob

    blob_path = await asyncio.to_thread(_write_payload_blob, payload)

    def _run() -> dict[str, Any]:
        exe = _spawn_python_executable() or sys.executable
        fd, result_path = tempfile.mkstemp(prefix="bt_engine_", suffix=".pkl")
        os.close(fd)
        backend = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [exe, "-m", "app.research.engine_backtest", blob_path, result_path],
            cwd=backend,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        last_beat = 0.0
        try:
            while proc.poll() is None:
                if should_cancel is not None and should_cancel():
                    proc.kill()
                    return {"status": "CANCELLED"}
                # Keeps the job watchdog fed; the engines report no bar progress.
                if progress_callback is not None and time.perf_counter() - last_beat >= 5.0:
                    try:
                        progress_callback({"phase": "ENGINE_WALK"})
                    except Exception:  # noqa: BLE001
                        pass
                    last_beat = time.perf_counter()
                time.sleep(0.05)
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            result_file = Path(result_path)
            if result_file.exists() and result_file.stat().st_size > 0:
                with result_file.open("rb") as handle:
                    return pickle.load(handle)
            raise OSError(0, f"Engine worker exited {proc.returncode} via {exe}. {stderr[-500:]}")
        finally:
            if proc.poll() is None:
                proc.kill()
            for p in (result_path, blob_path):
                try:
                    Path(p).unlink()
                except OSError:
                    pass

    return await asyncio.to_thread(_run)


async def engine_strategy_matrix(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    direction: str,
    limit: int,
    risk_usd: float,
    principal_usd: float,
    start_date: str | None,
    end_date: str | None,
    taker_fee: float,
    maker_fee: float,
    leverage: float,
    include_trades: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """One Backtest-screen cell for a ranked engine strategy. Same payload shape
    as ``BosResearchService.strategy_matrix``."""
    from app.research.service import _load_research_candles, _market_cap

    spec = ENGINE_STRATEGIES[strategy_id]
    direction_u = direction.upper().strip()
    if spec["family"] == DAILY_TREND_FAMILY:
        # Second line of the holdout guard (BacktestJobService.start is the first):
        # always a bounded range ending 2026-07-14, never the tail loader.
        start_date, end_date = daily_trend_dates(
            start_date, end_date, limit, bar_hours=int(spec.get("bar_hours", 24))
        )
    t0 = time.perf_counter()
    candles, eval_start, load_meta = await _load_research_candles(
        symbol,
        timeframe,
        limit=limit,
        start_date=start_date,
        end_date=end_date,
        warmup_bars=int(spec["warmup_bars"]),
        use_research_cache=False,
        tail_warmup=True,
    )
    if spec["family"] == DAILY_TREND_FAMILY and start_date is None:
        # "Max" (no start day) loads from the first stored bar, so nothing precedes
        # it: the first warm-up bars settle the indicators instead of being traded.
        eval_start = max(int(eval_start or 0), min(int(spec["warmup_bars"]), len(candles)))
    if progress_callback is not None:
        progress_callback({"phase": "RAW_ROWS_LOADED", "rows_loaded": len(candles)})
    sym_u = str(load_meta.get("symbol") or symbol)
    tf_u = str(load_meta.get("timeframe") or timeframe)
    out = await run_engine_cell_isolated(
        {
            "strategy_id": strategy_id,
            "direction": direction_u,
            "symbol": sym_u,
            "timeframe": tf_u,
            "candles": [dict(c) for c in candles],
            "index_start": int(eval_start or 0),
            "risk_usd": float(risk_usd),
            "taker_fee": float(taker_fee),
            "maker_fee": float(maker_fee),
            "leverage": float(leverage),
            "principal": float(principal_usd),
            "market_cap": _market_cap(sym_u),
        },
        should_cancel=should_cancel,
        progress_callback=progress_callback,
    )
    base = {
        "combination_id": strategy_id,
        "combination_name": spec["name"],
        "playbook": f"{spec['name']} ({spec['family']} engine)",
        "label": "RESEARCH ONLY",
        "disclaimer": (
            "Research only. Runs the strategy's own engine with its own fill model. "
            "No paper, live or Telegram path."
        ),
        "timing": {"engine_seconds": round(time.perf_counter() - t0, 3)},
    }
    if out.get("status") in ("CANCELLED", "ERROR"):
        return {**base, "status": out["status"], "reason": out.get("reason"), "rows": []}
    first_eval = candles[int(eval_start or 0)] if len(candles) > int(eval_start or 0) else None
    row = build_engine_row(
        out,
        strategy_id=strategy_id,
        direction=direction_u,
        symbol=sym_u,
        timeframe=tf_u,
        risk_usd=float(risk_usd),
        principal=float(principal_usd),
        leverage=float(leverage),
        bars_loaded=len(candles),
        warmup_bars=int(eval_start or 0),
        candle_source=load_meta.get("candle_source"),
        period_start=_iso(first_eval.get("time")) if first_eval else None,
        period_end=_iso(candles[-1].get("time")) if candles else None,
        include_trades=include_trades,
    )
    return {**base, "status": "OK", "rows": [row]}


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
