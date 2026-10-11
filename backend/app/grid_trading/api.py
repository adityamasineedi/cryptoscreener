"""Grid research routes. Every handler is 404 while GRID_TRADING_ENABLED is off."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.grid_trading.config import MARKET, GridConfig, GridConfigError, grid_trading_enabled
from app.grid_trading.engine import run_grid
from app.grid_trading.execution import GridExecution
from app.grid_trading.metrics import export_fills, summarize
from app.grid_trading.scanner import rank_scores, score_series
from app.grid_trading.validation import split_sample, sweep_grid_count, walk_forward

logger = get_logger("grid_trading.api")


class _FlaggedRoute(APIRoute):
    """404 before body validation while the feature flag is off."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def gated(request: Request):
            if not grid_trading_enabled():
                raise HTTPException(status_code=404, detail="grid trading disabled")
            return await original(request)

        return gated


router = APIRouter(prefix="/grid-trading", tags=["grid-trading"], route_class=_FlaggedRoute)
_execution = GridExecution()


def _require_enabled() -> None:
    if not grid_trading_enabled():
        raise HTTPException(status_code=404, detail="grid trading disabled")


def _config_error(exc: GridConfigError) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


class GridRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = ""
    timeframe: str = "1h"
    grid_type: str = "arithmetic"
    range_low: float
    range_high: float
    grid_count: int
    capital: float
    max_capital: float = 1_000_000.0
    fee_bps: float = 4.0
    taker_fee_bps: float | None = None
    slippage_bps: float = 1.0
    leverage: float = 1.0
    stop_price: float
    max_drawdown: float = 0.5
    max_open_orders: int = 50
    paused: bool = False
    funding_mode: str = "charge"
    intrabar_path: str = "worst"
    funding: list[dict[str, Any]] | None = None
    candles: list[dict[str, Any]] | None = None
    limit: int = Field(default=1500, ge=2, le=5000)
    after_time: str | float | int | None = None
    allow_overlap: bool = False
    is_fraction: float = 0.7
    train_bars: int = 80
    test_bars: int = 40
    grid_counts: list[int] = Field(default_factory=lambda: [4, 8, 12])


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeframe: str = "1h"
    symbols: list[str] = Field(default_factory=list)
    limit: int = Field(default=400, ge=30, le=2000)
    fee_bps: float = 4.0
    slippage_bps: float = 1.0
    grid_count: int = 8


def _to_config(body: GridRunRequest) -> GridConfig:
    try:
        return GridConfig(
            symbol=body.symbol,
            timeframe=body.timeframe,
            mode="backtest",
            grid_type=body.grid_type,  # type: ignore[arg-type]
            range_low=body.range_low,
            range_high=body.range_high,
            grid_count=body.grid_count,
            capital=body.capital,
            max_capital=body.max_capital,
            fee_bps=body.fee_bps,
            taker_fee_bps=body.taker_fee_bps,
            slippage_bps=body.slippage_bps,
            leverage=body.leverage,
            stop_price=body.stop_price,
            max_drawdown=body.max_drawdown,
            max_open_orders=body.max_open_orders,
            paused=body.paused,
            funding_mode=body.funding_mode,  # type: ignore[arg-type]
            intrabar_path=body.intrabar_path,  # type: ignore[arg-type]
            market=MARKET,
        )
    except Exception as exc:
        if isinstance(exc, GridConfigError):
            raise
        raise GridConfigError("config", str(exc)) from exc


def _time_key(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp()
    return float(value)


def apply_lookback(
    candles: list[dict[str, Any]],
    after_time: str | float | int | None,
    *,
    allow_overlap: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    """Drop the scanner window. Overlap is explicit and always warned."""
    if after_time is None:
        return candles, None
    try:
        cutoff = _time_key(after_time)
    except (TypeError, ValueError) as exc:
        raise GridConfigError("after_time", "after_time is not a timestamp") from exc
    later: list[dict[str, Any]] = []
    for bar in candles:
        try:
            stamp = _time_key(bar.get("time"))
        except (TypeError, ValueError):
            continue
        if stamp > cutoff:
            later.append(bar)
    if later:
        return later, None
    if allow_overlap:
        return candles, "Backtest overlaps the scanner lookback window."
    raise GridConfigError(
        "lookback",
        "No candles after the scanner lookback. Overlap reuses the window that chose the range.",
    )


async def _funding(
    body: GridRunRequest, candles: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Settlement rows only. Charge mode does not read funding_rates."""
    if body.funding_mode == "exclude":
        return None, None
    if body.funding is not None:
        return body.funding, None
    if not body.symbol or not candles:
        return None, None
    from app.grid_trading.settlements import load_grid_settlements

    loaded = await load_grid_settlements(body.symbol, candles[0]["time"], candles[-1]["time"])
    return loaded["rows"] or None, loaded.get("reason")


def _apply_funding_reason(result: dict[str, Any], reason: str | None) -> dict[str, Any]:
    if not reason:
        return result
    result["funding_status"] = "unavailable"
    result["net_pnl"] = None
    result["funding_included"] = False
    result["warnings"] = [reason]
    return result


async def _candles(body: GridRunRequest) -> tuple[list[dict[str, Any]], str | None]:
    if body.candles is not None:
        series = body.candles
    else:
        if not body.symbol:
            raise GridConfigError("symbol", "symbol is required when candles are omitted")
        from app.research.postgres_ohlcv import load_ohlcv_series_tail

        series = await load_ohlcv_series_tail(body.symbol, body.timeframe, limit=body.limit)
    return apply_lookback(series, body.after_time, allow_overlap=body.allow_overlap)


def _public_run(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": summarize(result),
        "levels": result["levels"],
        "fills": export_fills(result),
        "equity_curve": result["equity_curve"],
        "paths": result["paths"],
        "candles": result["candles"],
        "buy_and_hold": result["buy_and_hold"],
        "warnings": result["warnings"],
    }


@router.get("/status")
async def status() -> dict[str, Any]:
    _require_enabled()
    return {
        "enabled": True,
        "market": MARKET,
        "mode": "backtest",
        "live": False,
        "paper": False,
        "leverage": 1,
        "side": "long",
        "funding_included": False,
    }


@router.post("/backtest")
async def backtest(body: GridRunRequest) -> dict[str, Any]:
    _require_enabled()
    try:
        config = _to_config(body)
        candles, overlap = await _candles(body)
        funding, funding_reason = await _funding(body, candles)
        logger.info(
            "grid_backtest",
            symbol=config.symbol,
            timeframe=config.timeframe,
            bars=len(candles),
            market=MARKET,
        )
        result = _apply_funding_reason(run_grid(candles, config, funding), funding_reason)
    except GridConfigError as exc:
        raise _config_error(exc) from exc
    payload = _public_run(result)
    if overlap:
        payload["warnings"] = [overlap, *payload["warnings"]]
        payload["overlap"] = True
    return payload


@router.post("/validate")
async def validate(body: GridRunRequest) -> dict[str, Any]:
    _require_enabled()
    try:
        config = _to_config(body)
        candles, overlap = await _candles(body)
        funding, funding_reason = await _funding(body, candles)
        report = {
            "market": MARKET,
            "overlap": bool(overlap),
            "split": split_sample(candles, config, is_fraction=body.is_fraction, funding=funding),
            "walk_forward": walk_forward(
                candles,
                config,
                train_bars=body.train_bars,
                test_bars=body.test_bars,
                funding=funding,
            ),
            "sweep": sweep_grid_count(candles, config, body.grid_counts, funding),
        }
        if funding_reason:
            report["funding_status"] = "unavailable"
            report["reason"] = funding_reason
        if overlap:
            report["warning"] = overlap
        return report
    except GridConfigError as exc:
        raise _config_error(exc) from exc


@router.post("/scan")
async def scan(body: ScanRequest) -> dict[str, Any]:
    _require_enabled()
    from app.research.postgres_ohlcv import list_symbols_with_ohlcv, load_ohlcv_series_tail

    symbols = [item.strip().upper() for item in body.symbols if item.strip()]
    if not symbols:
        symbols = (await list_symbols_with_ohlcv(body.timeframe))[:20]
    rows = []
    for symbol in symbols:
        candles = await load_ohlcv_series_tail(symbol, body.timeframe, limit=body.limit)
        rows.append(
            score_series(
                candles,
                fee_bps=body.fee_bps,
                slippage_bps=body.slippage_bps,
                grid_count=body.grid_count,
                symbol=symbol,
                timeframe=body.timeframe,
            )
        )
    return {"market": MARKET, "timeframe": body.timeframe, "rows": rank_scores(rows)}


@router.post("/live")
async def live_order(order: dict[str, Any]) -> dict[str, Any]:
    _require_enabled()
    try:
        await _execution.submit_live(order)
    except GridConfigError as exc:
        raise _config_error(exc) from exc
    return {"ok": False}
