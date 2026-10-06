#!/usr/bin/env python3
"""COMBO_02_V2 multi-market validation — frozen strategy, no logic changes.

Loads all available historical OHLCV for the symbol set and aggregates
playbook / regime / symbol / monthly / best-worst trade reports.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]

# Wide bounds cover all currently stored history (DB probe: earliest ~2022-10).
DATA_START = "2022-01-01"
DATA_END = "2026-10-06"

PLAYBOOKS = ("TREND_FOLLOWING", "RANGE_MEAN_REVERSION", "REVERSAL")
REGIMES = (
    "BULL_TREND",
    "BEAR_TREND",
    "HIGH_VOLATILITY_TREND",
    "CHOPPY",
    "RANGE",
    "HIGH_VOLATILITY_RANGE",
    "LOW_VOLATILITY_COMPRESSION",
    "TRANSITION",
    "UNKNOWN",
)

OUT_DIR = Path("reports/combo02_v2_multi_market_validation")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month(ts: str | None) -> str:
    if not ts:
        return "UNKNOWN"
    s = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return f"{dt.year:04d}-{dt.month:02d}"
    except ValueError:
        return str(ts)[:7] if len(str(ts)) >= 7 else "UNKNOWN"


def _trade_row(symbol: str, t: dict[str, Any]) -> dict[str, Any]:
    snap = t.get("condition_snapshot") or {}
    v2 = snap.get("v2_diagnostics") if isinstance(snap.get("v2_diagnostics"), dict) else {}
    htf = snap.get("htf") if isinstance(snap.get("htf"), dict) else {}
    # Prefer nested diagnostics, then snapshot fields.
    return {
        "symbol": symbol,
        "timestamp": t.get("signal_time") or t.get("entry_time"),
        "exit_time": t.get("exit_time"),
        "direction": t.get("direction"),
        "playbook": snap.get("playbook") or v2.get("playbook"),
        "regime": snap.get("regime") or v2.get("regime"),
        "event": snap.get("event") or v2.get("event"),
        "confirmation": snap.get("confirmation") or v2.get("confirmation"),
        "htf_state": snap.get("htf_state") or v2.get("htf_state"),
        "trend_4h": htf.get("trend_4h") or snap.get("trend_4h"),
        "trend_1h": htf.get("trend_1h") or snap.get("trend_1h"),
        "entry": t.get("entry_price"),
        "stop": t.get("stop_price"),
        "tp1": t.get("tp1"),
        "tp2": t.get("tp2"),
        "tp3": t.get("tp3"),
        "r_multiple": _f(t.get("r_multiple")),
        "holding_bars": t.get("holding_bars"),
        "outcome": t.get("outcome"),
        "entry_index": t.get("entry_index"),
        "exit_index": t.get("exit_index"),
        "bos_direction": snap.get("bos_direction"),
        "sweep": snap.get("sweep") or v2.get("sweep"),
        "month": _month(t.get("signal_time") or t.get("entry_time")),
    }


def _bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    wins = sum(1 for x in rs if x > 0)
    losses = sum(1 for x in rs if x < 0)
    gross = sum(x for x in rs if x > 0)
    loss_abs = abs(sum(x for x in rs if x < 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    holds = [_f(r.get("holding_bars")) for r in rows]
    holds_ok = [h for h in holds if h is not None]
    long_n = sum(1 for r in rows if r.get("direction") == "LONG")
    short_n = sum(1 for r in rows if r.get("direction") == "SHORT")
    return {
        "trades": len(rows),
        "long": long_n,
        "short": short_n,
        "win_rate": (wins / len(rs)) if rs else None,
        "total_R": sum(rs) if rs else 0.0,
        "average_R": (sum(rs) / len(rs)) if rs else None,
        "profit_factor": (gross / loss_abs) if loss_abs > 0 else None,
        "maximum_drawdown_R": abs(max_dd) if rs else None,
        "average_holding_bars": (sum(holds_ok) / len(holds_ok)) if holds_ok else None,
        "wins": wins,
        "losses": losses,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {
                k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                for k, v in r.items()
            }
            w.writerow(flat)


def _loss_cause(row: dict[str, Any]) -> str:
    """Lightweight recurring-loss taxonomy from exported fields only."""
    play = str(row.get("playbook") or "UNKNOWN")
    regime = str(row.get("regime") or "UNKNOWN")
    direction = str(row.get("direction") or "?")
    event = str(row.get("event") or "")
    conf = str(row.get("confirmation") or "")
    htf = str(row.get("htf_state") or "")
    parts = [f"playbook={play}", f"regime={regime}", f"dir={direction}"]
    if "SWEEP" in event.upper():
        parts.append("event=SWEEP")
    elif "BOS" in event.upper():
        parts.append("event=BOS")
    elif "CHOCH" in event.upper():
        parts.append("event=CHOCH")
    if "OPTIONAL_PASS" in conf.upper() or "UNAVAILABLE" in conf.upper():
        parts.append("15m=weak_or_missing")
    if "NEUTRAL" in htf.upper():
        parts.append("htf=neutral_allowed")
    return "|".join(parts)


def _install_validation_context_patch() -> None:
    """Validation-only runtime patch: skip prebuilding the full 15m feature table.

    Frozen strategy files are not modified. 15M confirmation still uses the
    existing on-demand ``analyze_timeframe`` path (same as January parity check:
    5 trades unchanged). Avoids OOM on ~140k 15m bars.

    Also disk-caches the 1h feature table so variants/retries do not rebuild it
    (identical table for BASE / V2.1-A / V2.1-B).
    """
    import pickle
    from pathlib import Path

    from app.research.bos_strategy_comparison.htf import (
        build_htf_as_of_index_map_fully_closed,
    )
    from app.research.combo02_v2.context import Combo02V2Context
    from app.research.market_structure.structure import build_timeframe_feature_table

    cache_dir = Path("reports/combo02_v21_variant_compare/_ft_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(symbol: str, timeframe: str, setup_candles: Any) -> str:
        n = len(setup_candles)
        first = setup_candles[0].get("time") if n else None
        last = setup_candles[-1].get("time") if n else None
        return f"{symbol.upper()}_{timeframe}_{n}_{first}_{last}".replace(":", "-")

    @classmethod
    def build_for_validation(
        cls,
        *,
        symbol: str,
        setup_timeframe: str,
        setup_candles: Any,
        candles_15m: Any = None,
    ) -> Any:
        tf = setup_timeframe or "1h"
        series = list(setup_candles)
        key = _cache_key(symbol, tf, series)
        cache_path = cache_dir / f"{key}.pkl"
        t_build = time.perf_counter()
        table_1h = None
        if cache_path.exists():
            print(f"  loading cached 1h feature table for {symbol}...", flush=True)
            try:
                table_1h = pickle.loads(cache_path.read_bytes())
                print(
                    f"  1h feature table cache hit in {time.perf_counter() - t_build:.1f}s",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 — validation cache best-effort
                print(f"  cache load failed ({exc!r}); rebuilding...", flush=True)
                table_1h = None
        if table_1h is None:
            print(
                f"  building 1h feature table ({len(series)} bars) for {symbol}...",
                flush=True,
            )
            table_1h = build_timeframe_feature_table(
                series,
                tf,
                symbol=symbol,
            )
            print(
                f"  1h feature table done in {time.perf_counter() - t_build:.1f}s",
                flush=True,
            )
            try:
                cache_path.write_bytes(
                    pickle.dumps(table_1h, protocol=pickle.HIGHEST_PROTOCOL)
                )
                print(f"  cached 1h feature table -> {cache_path.name}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  cache write skipped ({exc!r})", flush=True)
        c15 = list(candles_15m or [])
        idx_map: list[int | None] = [None] * len(series)
        if c15:
            idx_map = build_htf_as_of_index_map_fully_closed(
                series,
                c15,
                setup_timeframe=tf,
                htf_timeframe="15m",
            )
        return cls(
            symbol=symbol.upper(),
            setup_timeframe=tf,
            table_1h=table_1h,
            candles_15m=c15,
            idx_15m_map=idx_map,
            table_15m=None,
        )

    Combo02V2Context.build = build_for_validation  # type: ignore[method-assign]


def _install_validation_15m_speed_patch() -> None:
    """Validation-only: incremental 15m swings + analyze cache (behavior-preserving).

    Full-history walks call ``detect_swings`` on ~140k 15m bars every 1h bar
    (quadratic). This replaces that with per-bar ``extend_swings`` + cached
    ``analyze_timeframe`` keyed by 15m as-of index. Strategy source files are
    not modified.
    """
    import app.research.combo02_v2.evaluate as evaluate_mod
    from app.signals.swing_detector import detect_swings as detect_swings_orig
    from app.signals.swing_detector import extend_swings
    from app.signals._candle_utils import series_ohlcv

    state: dict[str, Any] = {
        "cid": None,
        "as_of": -1,
        "swings": [],
        "highs": None,
        "lows": None,
        "closes": None,
        "analysis_by_j": {},
    }

    def reset_15m_cache() -> None:
        state["cid"] = None
        state["as_of"] = -1
        state["swings"] = []
        state["highs"] = None
        state["lows"] = None
        state["closes"] = None
        state["analysis_by_j"] = {}

    def detect_swings_incremental(
        candles: Any,
        *,
        left: int,
        right: int,
        symbol: str = "",
        timeframe: str = "",
        atr_period: int = 14,
        minimum_swing_distance_atr: float = 0.0,
        as_of_index: int | None = None,
        highs: Any = None,
        lows: Any = None,
        closes: Any = None,
    ) -> Any:
        if str(timeframe) != "15m" or as_of_index is None:
            return detect_swings_orig(
                candles,
                left=left,
                right=right,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=atr_period,
                minimum_swing_distance_atr=minimum_swing_distance_atr,
                as_of_index=as_of_index,
                highs=highs,
                lows=lows,
                closes=closes,
            )
        j = int(as_of_index)
        cid = id(candles)
        if state["cid"] != cid or j < state["as_of"] or state["as_of"] < 0:
            if state["cid"] != cid or state["highs"] is None:
                _, hs, ls, cs, _ = series_ohlcv(list(candles))
                state["highs"] = hs
                state["lows"] = ls
                state["closes"] = cs
                state["cid"] = cid
            state["swings"] = detect_swings_orig(
                candles,
                left=left,
                right=right,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=atr_period,
                minimum_swing_distance_atr=minimum_swing_distance_atr,
                as_of_index=j,
                highs=state["highs"],
                lows=state["lows"],
                closes=state["closes"],
            )
            state["as_of"] = j
            return state["swings"]
        if j == state["as_of"]:
            return state["swings"]
        for k in range(state["as_of"] + 1, j + 1):
            state["swings"] = extend_swings(
                state["swings"],
                candles,
                left=left,
                right=right,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=atr_period,
                minimum_swing_distance_atr=minimum_swing_distance_atr,
                as_of_index=k,
                highs=state["highs"],
                lows=state["lows"],
                closes=state["closes"],
            )
        state["as_of"] = j
        return state["swings"]

    evaluate_mod.detect_swings = detect_swings_incremental  # type: ignore[attr-defined]

    # Cache 15m analyze_timeframe by as_of index (identical inputs → identical out).
    from app.signals.signal_engine import SignalEngine

    _orig_analyze = SignalEngine.analyze_timeframe
    atr_series_cache: dict[int, list[Any]] = {}

    def analyze_cached(self: Any, symbol: str, timeframe: str, candles: Any, **kwargs: Any) -> Any:
        # Inject precomputed ATR for 15m (detect_bos otherwise O(n) Wilder ATR each bar).
        if str(timeframe) == "15m" and kwargs.get("atr_value") is None:
            from app.engines.mtf.indicators import atr_series
            from app.signals._candle_utils import series_ohlcv

            cid = id(candles)
            if cid not in atr_series_cache:
                _, hs, ls, cs, _ = series_ohlcv(list(candles))
                atr_series_cache[cid] = atr_series(
                    hs, ls, cs, int(getattr(self.config, "atr_period", 14) or 14)
                )
            j_atr = kwargs.get("as_of_index")
            if j_atr is not None:
                arr = atr_series_cache[cid]
                ji = int(j_atr)
                if 0 <= ji < len(arr) and arr[ji] is not None:
                    kwargs = dict(kwargs)
                    kwargs["atr_value"] = arr[ji]

        if str(timeframe) != "15m":
            return _orig_analyze(self, symbol, timeframe, candles, **kwargs)
        j = kwargs.get("as_of_index")
        if j is None:
            return _orig_analyze(self, symbol, timeframe, candles, **kwargs)
        key = (id(candles), int(j), id(kwargs.get("swings")))
        hit = state["analysis_by_j"].get(key)
        if hit is not None:
            return hit
        out = _orig_analyze(self, symbol, timeframe, candles, **kwargs)
        # Bound cache: keep last ~4096 entries
        if len(state["analysis_by_j"]) > 4096:
            state["analysis_by_j"].clear()
        state["analysis_by_j"][key] = out
        return out

    SignalEngine.analyze_timeframe = analyze_cached  # type: ignore[method-assign]

    def reset_all() -> None:
        reset_15m_cache()
        atr_series_cache.clear()

    _install_validation_15m_speed_patch.reset_15m_cache = reset_all  # type: ignore[attr-defined]


def _install_validation_htf_speed_patch() -> None:
    """Validation-only: precompute HTF trend cache + skip unused setup-TF trend.

    COMBO_02_V2 keeps ``require_htf_alignment=False``, so the backtest walk does
    not precompute HTF trends; each bar re-runs ``detect_swings`` on 1h/4h.
    Also ``htf_trends_for_setup_bar`` always computes a setup-bar trend labeled
    ``15m`` that v2 never reads. Both are accelerated here without changing
    strategy source files or soft-HTF allow/reject semantics.
    """
    from app.research.bos_strategy_comparison.htf import (
        build_htf_as_of_index_map,
        htf_trends_for_setup_bar as _htf_trends_orig,
        precompute_htf_trend_cache,
    )
    import app.research.combo02_v2.htf_policy as htf_policy

    state: dict[str, Any] = {
        "cache": None,
        "idx_1h": None,
        "idx_4h": None,
        "key": None,
    }

    def reset_htf_cache() -> None:
        state["cache"] = None
        state["idx_1h"] = None
        state["idx_4h"] = None
        state["key"] = None

    def htf_trends_skip_unused_setup_trend(**kwargs: Any) -> Any:
        if kwargs.get("trend_15m") is None:
            kwargs["trend_15m"] = "N/A"
        return _htf_trends_orig(**kwargs)

    htf_policy.htf_trends_for_setup_bar = htf_trends_skip_unused_setup_trend  # type: ignore[attr-defined]

    _orig_fetch = htf_policy.fetch_htf_trends

    def fetch_with_precompute(**kwargs: Any) -> Any:
        candles = kwargs.get("candles")
        c1h = kwargs.get("candles_1h")
        c4h = kwargs.get("candles_4h")
        scfg = kwargs.get("signal_config")
        symbol = str(kwargs.get("symbol") or "")
        key = (id(candles), id(c1h), id(c4h), symbol)
        if state["key"] != key and c1h is not None and c4h is not None and candles is not None:
            print("  precomputing HTF trend cache (validation-only)...", flush=True)
            t0 = time.perf_counter()
            cache: dict[tuple[str, int], str] = {}
            precompute_htf_trend_cache(
                list(c1h), timeframe="1h", symbol=symbol, config=scfg, cache=cache
            )
            precompute_htf_trend_cache(
                list(c4h), timeframe="4h", symbol=symbol, config=scfg, cache=cache
            )
            state["cache"] = cache
            state["idx_1h"] = build_htf_as_of_index_map(candles, c1h)
            state["idx_4h"] = build_htf_as_of_index_map(candles, c4h)
            state["key"] = key
            print(
                f"  HTF cache ready: entries={len(cache)} "
                f"in {time.perf_counter() - t0:.1f}s",
                flush=True,
            )
        if state["cache"] is not None:
            kwargs = dict(kwargs)
            kwargs["htf_trend_cache"] = state["cache"]
            if kwargs.get("htf_idx_1h_map") is None:
                kwargs["htf_idx_1h_map"] = state["idx_1h"]
            if kwargs.get("htf_idx_4h_map") is None:
                kwargs["htf_idx_4h_map"] = state["idx_4h"]
        return _orig_fetch(**kwargs)

    htf_policy.fetch_htf_trends = fetch_with_precompute  # type: ignore[attr-defined]
    # evaluate.py imports fetch_htf_trends by name — patch there too
    import app.research.combo02_v2.evaluate as evaluate_mod

    evaluate_mod.fetch_htf_trends = fetch_with_precompute  # type: ignore[attr-defined]
    _install_validation_htf_speed_patch.reset_htf_cache = reset_htf_cache  # type: ignore[attr-defined]


def _install_validation_ohlcv_cache_patch() -> None:
    """Validation-only: cache full-series OHLCV arrays for prefix slices.

    ``detect_bos`` / impulse / retest rebuild ``series_ohlcv(list(candles[:end+1]))``
    on every bar (O(n²)). Register full 1h/15m series once, then serve prefixes
    by identity of candle dicts. Strategy files untouched; values identical.
    """
    from app.signals._candle_utils import series_ohlcv as _series_ohlcv_orig
    import app.signals.bos_engine as bos_engine
    import app.signals.impulse_engine as impulse_engine
    import app.signals.retest_engine as retest_engine
    import app.signals.swing_detector as swing_detector

    registry: list[tuple[Any, tuple]] = []

    def register_series(candles: Any) -> None:
        series = list(candles)
        arrays = _series_ohlcv_orig(series)
        registry.append((series, arrays))

    def reset_ohlcv_registry() -> None:
        registry.clear()

    def series_ohlcv_cached(candles: Any) -> Any:
        seq = list(candles) if not isinstance(candles, list) else candles
        if not seq:
            return _series_ohlcv_orig(seq)
        n = len(seq)
        first = seq[0]
        last = seq[-1]
        for full, arrays in registry:
            if n <= len(full) and full[0] is first and full[n - 1] is last:
                o, h, l, c, v = arrays
                return o[:n], h[:n], l[:n], c[:n], v[:n]
        return _series_ohlcv_orig(seq)

    for mod in (bos_engine, impulse_engine, retest_engine, swing_detector):
        mod.series_ohlcv = series_ohlcv_cached  # type: ignore[attr-defined]

    _install_validation_ohlcv_cache_patch.register_series = register_series  # type: ignore[attr-defined]
    _install_validation_ohlcv_cache_patch.reset_ohlcv_registry = reset_ohlcv_registry  # type: ignore[attr-defined]


async def _run_symbol(symbol: str) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.signals.config import SignalConfig

    t0 = time.perf_counter()
    print(f"  loading candles {symbol}...", flush=True)
    c1h, eval_start, meta1 = await _load_research_candles(
        symbol,
        "1h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=100,
    )
    c4h, _, meta4 = await _load_research_candles(
        symbol,
        "4h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=50,
    )
    c15m, _, meta15 = await _load_research_candles(
        symbol,
        "15m",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=200,
    )
    # Availability probes only (frozen v2 does not consume 5m/1d for entries).
    avail: dict[str, Any] = {"5m": None, "1d": None}
    for tf in ("5m", "1d"):
        try:
            series, _, meta = await _load_research_candles(
                symbol,
                tf,
                start_date=DATA_START,
                end_date=DATA_END,
                use_research_cache=True,
                warmup_bars=0,
            )
            avail[tf] = {
                "bars": len(series),
                "source": meta.get("source"),
            }
        except Exception as exc:  # noqa: BLE001
            avail[tf] = {"bars": 0, "error": str(exc)}

    combo = get_combination("COMBO_02_V2")
    assert combo is not None
    idx0 = eval_start if eval_start and eval_start > 0 else None
    reset_15m = getattr(_install_validation_15m_speed_patch, "reset_15m_cache", None)
    if callable(reset_15m):
        reset_15m()
    reset_htf = getattr(_install_validation_htf_speed_patch, "reset_htf_cache", None)
    if callable(reset_htf):
        reset_htf()
    reset_ohlcv = getattr(
        _install_validation_ohlcv_cache_patch, "reset_ohlcv_registry", None
    )
    if callable(reset_ohlcv):
        reset_ohlcv()
    register = getattr(_install_validation_ohlcv_cache_patch, "register_series", None)
    if callable(register):
        register(c1h)
        register(c15m)
        register(c4h)
    print(
        f"  backtesting {symbol}: 1h={len(c1h)} 4h={len(c4h)} 15m={len(c15m)}...",
        flush=True,
    )
    last_prog = {"t": time.perf_counter(), "bars": 0}

    def _progress(payload: dict[str, Any]) -> None:
        bars = int(payload.get("bars_processed") or payload.get("bars") or 0)
        total = int(payload.get("total_bars") or payload.get("total") or 0)
        trades_n = payload.get("trades")
        if trades_n is None:
            trades_n = payload.get("trades_n")
        phase = str(payload.get("phase") or "")
        now = time.perf_counter()
        if bars - last_prog["bars"] >= 200 or now - last_prog["t"] >= 10.0:
            elapsed = max(now - last_prog["t"], 1e-6)
            delta = max(bars - last_prog["bars"], 0)
            rate = delta / elapsed
            print(
                f"  [{symbol}] {phase} bars={bars}/{total} "
                f"trades={trades_n} ({rate:.1f} bars/s)",
                flush=True,
            )
            last_prog["t"] = now
            last_prog["bars"] = bars

    out = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        candles_15m=c15m,
        direction_filter=None,
        index_start=idx0,
        progress_callback=_progress,
    )
    closed = [
        t
        for t in (out.get("trades") or [])
        if t.get("outcome") not in (None, "OPEN")
    ]
    rows = [_trade_row(symbol, t) for t in closed]
    elapsed = time.perf_counter() - t0
    return {
        "symbol": symbol,
        "bars_1h": len(c1h),
        "bars_4h": len(c4h),
        "bars_15m": len(c15m),
        "eval_start": eval_start,
        "source_1h": meta1.get("source"),
        "source_4h": meta4.get("source"),
        "source_15m": meta15.get("source"),
        "availability_5m_1d": avail,
        "configuration_hash": out.get("configuration_hash"),
        "elapsed_seconds": round(elapsed, 3),
        "sample_size": out.get("sample_size"),
        "metrics": _bucket_metrics(rows),
        "trades": rows,
    }


def _build_report(per_symbol: list[dict[str, Any]]) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    for s in per_symbol:
        all_trades.extend(s.get("trades") or [])

    overall = _bucket_metrics(all_trades)

    by_playbook = {}
    for pb in PLAYBOOKS:
        rows = [t for t in all_trades if t.get("playbook") == pb]
        by_playbook[pb] = _bucket_metrics(rows)

    by_regime = {}
    for reg in REGIMES:
        rows = [t for t in all_trades if str(t.get("regime") or "UNKNOWN") == reg]
        m = _bucket_metrics(rows)
        by_regime[reg] = {
            "trades": m["trades"],
            "total_R": m["total_R"],
            "average_R": m["average_R"],
            "win_rate": m["win_rate"],
        }

    by_symbol = {
        s["symbol"]: {
            **s["metrics"],
            "bars_1h": s["bars_1h"],
            "elapsed_seconds": s["elapsed_seconds"],
            "period_hint": {
                "source_1h": s.get("source_1h"),
                "availability_5m_1d": s.get("availability_5m_1d"),
            },
        }
        for s in per_symbol
    }

    ranked = sorted(
        [t for t in all_trades if t.get("r_multiple") is not None],
        key=lambda t: float(t["r_multiple"]),
    )
    worst = ranked[:10]
    best = list(reversed(ranked[-10:])) if ranked else []

    by_month: dict[str, dict[str, Any]] = {}
    month_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in all_trades:
        month_groups[str(t.get("month") or "UNKNOWN")].append(t)
    for month in sorted(month_groups):
        by_month[month] = _bucket_metrics(month_groups[month])

    long_rows = [t for t in all_trades if t.get("direction") == "LONG"]
    short_rows = [t for t in all_trades if t.get("direction") == "SHORT"]

    pos_play = [k for k, v in by_playbook.items() if (v.get("total_R") or 0) > 0]
    neg_play = [k for k, v in by_playbook.items() if (v.get("total_R") or 0) < 0]
    pos_reg = [k for k, v in by_regime.items() if (v.get("total_R") or 0) > 0]
    neg_reg = [k for k, v in by_regime.items() if (v.get("total_R") or 0) < 0]
    pos_sym = [k for k, v in by_symbol.items() if (v.get("total_R") or 0) > 0]
    neg_sym = [k for k, v in by_symbol.items() if (v.get("total_R") or 0) < 0]

    loss_rows = [t for t in all_trades if (t.get("r_multiple") or 0) < 0]
    cause_counts: dict[str, int] = defaultdict(int)
    for t in loss_rows:
        cause_counts[_loss_cause(t)] += 1
    top_causes = sorted(cause_counts.items(), key=lambda x: -x[1])[:15]

    # Simplified recurring themes
    theme_counts: dict[str, int] = defaultdict(int)
    for t in loss_rows:
        theme_counts[f"playbook:{t.get('playbook')}"] += 1
        theme_counts[f"regime:{t.get('regime')}"] += 1
        theme_counts[f"direction:{t.get('direction')}"] += 1
        conf = str(t.get("confirmation") or "")
        if "OPTIONAL_PASS" in conf or "UNAVAILABLE" in conf:
            theme_counts["theme:15m_weak_or_missing"] += 1
        event = str(t.get("event") or "")
        if "SWEEP" in event.upper():
            theme_counts["theme:sweep_entry"] += 1
        if "NEUTRAL" in str(t.get("htf_state") or "").upper():
            theme_counts["theme:htf_neutral_allowed"] += 1
    top_themes = sorted(theme_counts.items(), key=lambda x: -x[1])[:12]

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "combination_id": "COMBO_02_V2",
        "data_start": DATA_START,
        "data_end": DATA_END,
        "symbols": SYMBOLS,
        "config": {
            "signal_config": "SignalConfig()",
            "research_config": "ResearchConfig()",
            "direction_filter": None,
            "note": "Same configuration as January COMBO_02_V2 comparison",
        },
        "disclaimer": (
            "Validation only — frozen COMBO_02_V2. No parameter optimization. "
            "Trade count and R totals are not a profitability claim."
        ),
        "overall": overall,
        "by_playbook": by_playbook,
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "by_month": by_month,
        "long_vs_short": {
            "LONG": _bucket_metrics(long_rows),
            "SHORT": _bucket_metrics(short_rows),
        },
        "best_trades": best,
        "worst_trades": worst,
        "validation_signs": {
            "playbooks_positive_total_R": pos_play,
            "playbooks_negative_total_R": neg_play,
            "regimes_positive_total_R": pos_reg,
            "regimes_negative_total_R": neg_reg,
            "symbols_positive_total_R": pos_sym,
            "symbols_negative_total_R": neg_sym,
        },
        "loss_cause_counts": [
            {"cause": k, "count": v} for k, v in top_causes
        ],
        "loss_theme_counts": [
            {"theme": k, "count": v} for k, v in top_themes
        ],
        "per_symbol_runtime": [
            {
                "symbol": s["symbol"],
                "bars_1h": s["bars_1h"],
                "trades": s["metrics"]["trades"],
                "elapsed_seconds": s["elapsed_seconds"],
                "configuration_hash": s.get("configuration_hash"),
            }
            for s in per_symbol
        ],
    }


def _format_md(report: dict[str, Any]) -> str:
    o = report["overall"]
    lines = [
        "# COMBO_02_V2 Multi-Market Validation",
        "",
        report["disclaimer"],
        "",
        f"Generated: {report['generated_at_utc']}",
        f"Data window requested: {report['data_start']} -> {report['data_end']}",
        f"Symbols: {', '.join(report['symbols'])}",
        "",
        "## 1. Overall",
        "",
        f"| Metric | Value |",
        f"|---|---:|",
        f"| Total trades | {o['trades']} |",
        f"| LONG | {o['long']} |",
        f"| SHORT | {o['short']} |",
        f"| Win rate | {o['win_rate']} |",
        f"| Total R | {o['total_R']} |",
        f"| Average R | {o['average_R']} |",
        f"| Profit factor | {o['profit_factor']} |",
        f"| Max drawdown R | {o['maximum_drawdown_R']} |",
        f"| Avg holding bars | {o['average_holding_bars']} |",
        "",
        "## 2. Playbook performance",
        "",
    ]
    for pb, m in report["by_playbook"].items():
        lines.extend(
            [
                f"### {pb}",
                "",
                f"- trades: {m['trades']} (LONG {m['long']} / SHORT {m['short']})",
                f"- win rate: {m['win_rate']}",
                f"- total R: {m['total_R']}",
                f"- average R: {m['average_R']}",
                f"- profit factor: {m['profit_factor']}",
                f"- max drawdown R: {m['maximum_drawdown_R']}",
                "",
            ]
        )
    lines.extend(["## 3. Regime performance", ""])
    lines.append("| Regime | Trades | Total R | Avg R | Win rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for reg, m in report["by_regime"].items():
        lines.append(
            f"| {reg} | {m['trades']} | {m['total_R']} | {m['average_R']} | {m['win_rate']} |"
        )
    lines.extend(["", "## 4. Symbol performance", ""])
    lines.append(
        "| Symbol | Trades | LONG | SHORT | Win rate | Total R | Avg R | PF | Max DD |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sym, m in report["by_symbol"].items():
        lines.append(
            f"| {sym} | {m['trades']} | {m['long']} | {m['short']} | {m['win_rate']} | "
            f"{m['total_R']} | {m['average_R']} | {m['profit_factor']} | "
            f"{m['maximum_drawdown_R']} |"
        )
    lines.extend(["", "## 5. Validation signs", ""])
    vs = report["validation_signs"]
    for k, v in vs.items():
        lines.append(f"- **{k}**: {', '.join(v) if v else '(none)'}")
    lines.extend(["", "## 6. LONG vs SHORT", ""])
    for d, m in report["long_vs_short"].items():
        lines.append(
            f"- {d}: trades={m['trades']} total_R={m['total_R']} "
            f"avg_R={m['average_R']} win_rate={m['win_rate']} pf={m['profit_factor']}"
        )
    lines.extend(["", "## 7. Monthly breakdown", ""])
    lines.append("| Month | Trades | Total R | Avg R | Win rate |")
    lines.append("|---|---:|---:|---:|---:|")
    for month, m in report["by_month"].items():
        lines.append(
            f"| {month} | {m['trades']} | {m['total_R']} | {m['average_R']} | {m['win_rate']} |"
        )
    lines.extend(["", "## 8. Loss themes (recurring)", ""])
    for item in report["loss_theme_counts"][:10]:
        lines.append(f"- {item['theme']}: {item['count']}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `validation_report.json`",
            "- `all_trades.csv`",
            "- `best_trades.csv`",
            "- `worst_trades.csv`",
            "- `by_month.csv`",
            "",
        ]
    )
    return "\n".join(lines)


def _persist_symbol_result(result: dict[str, Any]) -> None:
    sym = result["symbol"]
    (OUT_DIR / f"{sym}_result.json").write_text(
        json.dumps(
            {k: v for k, v in result.items() if k != "trades"}
            | {"trade_count": len(result.get("trades") or [])},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    _write_csv(OUT_DIR / f"{sym}_trades.csv", result.get("trades") or [])


async def main() -> int:
    from app.config import get_settings
    from app.services.database import db_manager

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings())
    _install_validation_context_patch()
    _install_validation_15m_speed_patch()
    _install_validation_htf_speed_patch()
    _install_validation_ohlcv_cache_patch()
    print(
        "Installed validation-only patches "
        "(no 15m feature-table prebuild; incremental 15m swings; "
        "HTF trend cache; OHLCV prefix cache; strategy files untouched).",
        flush=True,
    )

    per_symbol: list[dict[str, Any]] = []
    # Sequential: 1h feature tables are still heavy; avoid OOM.
    for sym in SYMBOLS:
        print(f"=== Running {sym} ===", flush=True)
        result = await _run_symbol(sym)
        _persist_symbol_result(result)
        print(
            f"{sym}: bars={result['bars_1h']} trades={result['metrics']['trades']} "
            f"total_R={result['metrics']['total_R']:.3f} "
            f"elapsed={result['elapsed_seconds']}s",
            flush=True,
        )
        per_symbol.append(result)

    report = _build_report(per_symbol)
    all_trades = []
    for s in per_symbol:
        all_trades.extend(s.get("trades") or [])

    (OUT_DIR / "validation_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "VALIDATION_REPORT.md").write_text(
        _format_md(report), encoding="utf-8"
    )
    _write_csv(OUT_DIR / "all_trades.csv", all_trades)
    _write_csv(OUT_DIR / "best_trades.csv", report["best_trades"])
    _write_csv(OUT_DIR / "worst_trades.csv", report["worst_trades"])
    month_rows = [
        {"month": m, **metrics} for m, metrics in report["by_month"].items()
    ]
    _write_csv(OUT_DIR / "by_month.csv", month_rows)

    try:
        print("\n" + _format_md(report), flush=True)
    except UnicodeEncodeError:
        print(
            f"\nReport written to {OUT_DIR / 'VALIDATION_REPORT.md'} "
            "(console encoding cannot print full markdown).",
            flush=True,
        )
    print(f"\nWrote artifacts under {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
