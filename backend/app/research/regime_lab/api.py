"""HTTP surface for Regime Lab. Runs are in-memory background jobs; nothing is persisted."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, fields, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.research.intraday_bos_sd.spec import STABLE_BASES, WRAPPED_BASES
from app.research.postgres_ohlcv import load_ohlcv_series_range
from app.research.regime_lab.config import (
    BTC_SYMBOL,
    DAILY_REGIME_PARAMS_OVERRIDES,
    HOLDOUT_START,
    RECOMMENDED_SAFEGUARDS,
    REGIME_METHODS,
    LIQUIDITY_LOOKBACK_DAYS,
    STRATEGIES,
    TIERS,
    RegimeParams,
    RunConfig,
    Safeguards,
    StrategyParams,
    config_document,
    warmup_bars,
)
from app.research.regime_lab.engine import TF_DELTA, run_backtest
from app.research.regime_lab.strategies import SymbolData
from app.research.regime_lab.walkforward import run_walk_forward
from app.services.database import db_manager

router = APIRouter(prefix="/research/regime-lab", tags=["regime-lab"])

MAX_JOBS = 10
_jobs: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()


class RunRequest(BaseModel):
    mode: Literal["backtest", "walk_forward"] = "backtest"
    tier: Literal["large", "mid", "small"] = "large"
    timeframe: Literal["1h", "4h", "1d"] = "4h"
    symbols: list[str] | None = None
    start: date = date(2023, 6, 1)
    end: date | None = None
    include_holdout: bool = False
    strategies: list[str] = Field(default_factory=lambda: list(STRATEGIES))
    initial_equity: float = Field(10_000.0, gt=0)
    risk_pct: float | None = Field(None, gt=0, le=0.02)
    stop_atr_mult: float | None = Field(None, gt=0, le=10)
    slippage: float | None = Field(None, ge=0, le=0.02)
    fee_rate: float = Field(0.0004, ge=0, le=0.01)
    max_leverage: float = Field(2.0, gt=0, le=10)
    btc_gate: bool = True
    allow_short: bool | None = None
    train_months: int = Field(12, ge=3, le=36)
    test_months: int = Field(3, ge=1, le=12)
    params: dict[str, Any] = Field(default_factory=dict)
    regime: dict[str, Any] = Field(default_factory=dict)
    safeguard_preset: Literal["off", "recommended"] = "off"
    safeguards: dict[str, Any] = Field(default_factory=dict)  # overrides on top of the preset
    symbol_slippage: dict[str, float] = Field(default_factory=dict)
    symbol_timeframes: dict[str, Literal["1h", "4h", "1d"]] = Field(default_factory=dict)


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _dataclass_overrides(cls, base, overrides: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    unknown = set(overrides) - names
    if unknown:
        raise HTTPException(422, f"unknown {cls.__name__} fields: {sorted(unknown)}")
    return replace(base, **overrides)


def build_config(req: RunRequest) -> RunConfig:
    bad = [s for s in req.strategies if s not in STRATEGIES]
    if bad or not req.strategies:
        raise HTTPException(422, f"strategies must be a non-empty subset of {list(STRATEGIES)}")
    preset = RECOMMENDED_SAFEGUARDS if req.safeguard_preset == "recommended" else Safeguards()
    sg = _dataclass_overrides(Safeguards, preset, req.safeguards)
    if sg.regime_method not in REGIME_METHODS:
        raise HTTPException(422, f"regime_method must be one of {list(REGIME_METHODS)}")
    if sg.wf_selection not in ("best", "plateau"):
        raise HTTPException(422, "wf_selection must be best or plateau")
    if not 0 <= sg.perf_cut <= 1 or not 0 <= sg.transition_size <= 1:
        raise HTTPException(422, "perf_cut and transition_size must be between 0 and 1")
    if req.timeframe == "1d" and sg.daily_regime:
        raise HTTPException(422, "daily_regime needs an intraday timeframe")
    params = _dataclass_overrides(StrategyParams, StrategyParams(), req.params)
    if params.rsi_exit_mode not in ("sma", "rsi"):
        raise HTTPException(422, "rsi_exit_mode must be sma or rsi")
    if params.donchian_entry_mode not in ("breakout", "retest_level", "retest_mid"):
        raise HTTPException(422, "donchian_entry_mode must be breakout, retest_level or retest_mid")
    if sg.daily_loss_basis not in ("equity", "realized"):
        raise HTTPException(422, "daily_loss_basis must be equity or realized")
    if any(not 0 <= v <= 0.02 for v in req.symbol_slippage.values()):
        raise HTTPException(422, "symbol_slippage values must be between 0 and 0.02")
    return RunConfig(
        tier=req.tier,
        timeframe=req.timeframe,
        strategies=tuple(req.strategies),
        initial_equity=req.initial_equity,
        risk_pct=req.risk_pct,
        stop_atr_mult=req.stop_atr_mult,
        slippage=req.slippage,
        fee_rate=req.fee_rate,
        max_leverage=req.max_leverage,
        btc_gate=req.btc_gate,
        allow_short=req.allow_short,
        regime=_dataclass_overrides(RegimeParams, RegimeParams(), req.regime),
        params=params,
        safeguards=sg,
        symbol_slippage={k.upper(): v for k, v in req.symbol_slippage.items()},
        symbol_timeframes={k.upper(): v for k, v in req.symbol_timeframes.items()},
    )


def _window(req: RunRequest) -> tuple[datetime, datetime]:
    start = _utc(req.start)
    end = _utc(req.end) if req.end else datetime.now(timezone.utc)
    if not req.include_holdout:
        end = min(end, HOLDOUT_START)
    if end <= start:
        raise HTTPException(422, "end must be after start (the holdout is excluded unless include_holdout)")
    return start, end


def _tradable_base(symbol: str) -> bool:
    if not symbol.endswith("USDT"):
        return False
    base = symbol[: -len("USDT")]
    return base not in STABLE_BASES and base not in WRAPPED_BASES and not base.endswith(("UP", "DOWN"))


async def rank_by_liquidity(as_of: datetime, timeframe: str) -> list[dict[str, Any]]:
    """Mean daily dollar volume over the lookback before `as_of`; only data before `as_of` is used."""
    if db_manager.engine is None:
        raise HTTPException(503, "database unavailable")
    lookback_start = as_of - timedelta(days=LIQUIDITY_LOOKBACK_DAYS)
    sql = text(
        """
        SELECT d.symbol, AVG(d.close * d.volume) AS dollar_volume, COUNT(*) AS days
        FROM ohlcv d
        WHERE d.timeframe = '1d' AND d.time >= :lo AND d.time < :hi
          AND EXISTS (
            SELECT 1 FROM ohlcv t
            WHERE t.symbol = d.symbol AND t.timeframe = :tf AND t.time < :hi
          )
        GROUP BY d.symbol
        HAVING COUNT(*) >= :min_days
        ORDER BY dollar_volume DESC
        """
    )
    async with db_manager.engine.begin() as conn:
        rows = (
            await conn.execute(
                sql,
                {"lo": lookback_start, "hi": as_of, "tf": timeframe, "min_days": int(LIQUIDITY_LOOKBACK_DAYS * 0.66)},
            )
        ).fetchall()
    ranked = [r for r in rows if _tradable_base(str(r[0]).upper())]
    return [
        {"rank": k + 1, "symbol": str(r[0]).upper(), "dollar_volume": float(r[1] or 0), "days": int(r[2])}
        for k, r in enumerate(ranked)
    ]


async def resolve_universe(req: RunRequest, start: datetime) -> tuple[list[str], list[dict[str, Any]]]:
    if req.symbols:
        return [s.strip().upper() for s in req.symbols if s.strip()], []
    ranked = await rank_by_liquidity(start, req.timeframe)
    tier = TIERS[req.tier]
    picked = [r for r in ranked if tier.rank_min <= r["rank"] <= tier.rank_max]
    return [r["symbol"] for r in picked], picked


async def _load_symbol(
    sym: str, cfg: RunConfig, start: datetime, end: datetime, timeframe: str | None = None
) -> SymbolData | None:
    tf = timeframe or cfg.timeframe
    candles = await load_ohlcv_series_range(
        sym, tf, start=start, end_exclusive=end, warmup_bars=warmup_bars(cfg)
    )
    if not candles:
        return None
    d = SymbolData.from_candles(sym, candles, cfg.regime, cfg.safeguards)
    if tf != cfg.timeframe:
        d.bar_delta = TF_DELTA[tf]
    if cfg.safeguards.daily_regime and tf != "1d":
        daily_params = replace(cfg.regime, **DAILY_REGIME_PARAMS_OVERRIDES)
        daily_cfg = replace(cfg, regime=daily_params)
        daily = await load_ohlcv_series_range(
            sym, "1d", start=start, end_exclusive=end, warmup_bars=warmup_bars(daily_cfg)
        )
        htf = SymbolData.from_candles(sym, daily, daily_params, cfg.safeguards)
        d.use_higher_timeframe_regime(htf, TF_DELTA[tf], TF_DELTA["1d"])
    return d


async def load_data(
    symbols: list[str], cfg: RunConfig, start: datetime, end: datetime
) -> tuple[dict[str, SymbolData], SymbolData | None, list[dict[str, Any]]]:
    datas: dict[str, SymbolData] = {}
    coverage: list[dict[str, Any]] = []
    for sym in symbols:
        d = await _load_symbol(sym, cfg, start, end, cfg.symbol_timeframes.get(sym))
        if d is None:
            coverage.append({"symbol": sym, "bars": 0, "note": "no data"})
            continue
        datas[sym] = d
        first_ready = next((t for t, r in zip(d.times, d.regime) if r != "WARMUP"), None)
        coverage.append(
            {
                "symbol": sym,
                "bars": len(d.times),
                "first_bar": d.times[0].isoformat(),
                "last_bar": d.times[-1].isoformat(),
                "first_regime_bar": first_ready.isoformat() if first_ready else None,
                "late_start": bool(first_ready and first_ready > start),
            }
        )
    sg = cfg.safeguards
    # The BTC gate, crash filter and correlation read BTC on the run timeframe,
    # even when BTC itself trades on another one.
    btc = datas.get(BTC_SYMBOL) if BTC_SYMBOL not in cfg.symbol_timeframes else None
    if btc is None and (cfg.btc_gate or sg.crash_filter or sg.corr_threshold > 0):
        btc = await _load_symbol(BTC_SYMBOL, cfg, start, end)
    return datas, btc, coverage


def _trim_jobs() -> None:
    while len(_jobs) > MAX_JOBS:
        oldest = min(_jobs, key=lambda k: _jobs[k]["created_at"])
        if _jobs[oldest]["status"] == "running":
            break
        _jobs.pop(oldest)


async def _execute(job_id: str, req: RunRequest) -> None:
    job = _jobs[job_id]

    def progress(frac: float, stage: str) -> None:
        job["progress"] = round(frac, 4)
        job["stage"] = stage

    try:
        cfg = build_config(req)
        start, end = _window(req)
        progress(0.0, "ranking universe")
        symbols, ranking = await resolve_universe(req, start)
        if not symbols:
            raise ValueError("no symbols for this tier and start date")
        progress(0.0, "loading candles")
        datas, btc, coverage = await load_data(symbols, cfg, start, end)
        if not datas:
            raise ValueError("no candles loaded")
        progress(0.0, "simulating")
        if req.mode == "walk_forward":
            result = await asyncio.to_thread(
                run_walk_forward, datas, cfg, start, end,
                btc=btc, train_months=req.train_months, test_months=req.test_months, progress=progress,
            )
        else:
            result = await asyncio.to_thread(run_backtest, datas, cfg, start, end, btc=btc)
        result.pop("_curve", None)
        result["universe"] = {"symbols": list(datas), "ranking": ranking, "coverage": coverage}
        result["window"] = {"start": start.isoformat(), "end": end.isoformat(), "holdout_start": HOLDOUT_START.isoformat()}
        result["effective"] = {
            "risk_pct": cfg.eff_risk_pct,
            "stop_atr_mult": cfg.eff_stop_mult,
            "slippage": cfg.eff_slippage,
            "fee_rate": cfg.fee_rate,
            "allow_short": cfg.eff_allow_short,
            "strategies": list(cfg.eff_strategies),
            "btc_gate": cfg.btc_gate and btc is not None,
            "safeguards": asdict(cfg.safeguards),
        }
        job.update(status="done", progress=1.0, stage="done", result=result)
    except HTTPException as exc:
        job.update(status="error", error=str(exc.detail))
    except Exception as exc:  # surfaced to the UI; the job never crashes the API
        job.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        job["finished_at"] = time.time()


@router.get("/config")
async def get_config() -> dict:
    return config_document()


@router.get("/universe")
async def get_universe(start: date = date(2023, 6, 1), timeframe: str = "4h") -> dict:
    ranked = await rank_by_liquidity(_utc(start), timeframe)
    tiers = {
        name: [r["symbol"] for r in ranked if t.rank_min <= r["rank"] <= t.rank_max]
        for name, t in TIERS.items()
    }
    return {"as_of": _utc(start).isoformat(), "lookback_days": LIQUIDITY_LOOKBACK_DAYS, "tiers": tiers, "ranking": ranked[:60]}


@router.post("/run")
async def start_run(req: RunRequest) -> dict:
    build_config(req)
    _window(req)
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "progress": 0.0,
        "stage": "queued",
        "request": req.model_dump(mode="json"),
        "created_at": time.time(),
        "result": None,
        "error": None,
    }
    _trim_jobs()
    task = asyncio.create_task(_execute(job_id, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job (jobs live in memory and reset on restart)")
    return job


@router.get("/jobs")
async def list_jobs() -> dict:
    rows = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return {
        "jobs": [
            {k: j[k] for k in ("job_id", "status", "progress", "stage", "request", "created_at", "error")}
            | {"summary": (j.get("result") or {}).get("summary")}
            for j in rows
        ]
    }
