"""API facade for multi-cap research strategies.

Research only — never imports into live signal engine paths.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.logging import get_logger
from app.research.multi_cap_strategies.adapter import (
    MultiCapStrategyAdapter,
    list_strategy_definitions,
)
from app.research.multi_cap_strategies.cap_filter import market_cap_from_store
from app.research.multi_cap_strategies.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.repository import load_run, save_run
from app.research.multi_cap_strategies.runner import aggregate_runs, run_strategy_on_series
from app.research.postgres_ohlcv import (
    list_symbols_with_ohlcv,
    load_ohlcv_series_range,
    load_ohlcv_series_tail,
)
from app.research.query_utils import (
    normalize_research_symbol,
    normalize_research_timeframe,
    resolve_date_bounds,
    slice_candles_for_research,
)

logger = get_logger("multi_cap_strategies_service")


class MultiCapStrategiesService:
    def list_strategies(self) -> dict[str, Any]:
        return {
            "status": "OK",
            "strategies": list_strategy_definitions(),
            "research_engine_version": RESEARCH_ENGINE_VERSION,
            "disclaimer": DISCLAIMER,
            "label": "Historical Result",
            "note": (
                "Research only — live engine unchanged. "
                "S1/S2/S3/C1-C4 unchanged. No winner ranking."
            ),
            "reused_components": [
                "postgres_ohlcv",
                "classify_asset_group",
                "combination_backtest.evaluate_candidate_trades",
                "trade_fees",
                "metrics.compute_metrics",
                "split_period_indices",
                "data_quality.verify_ohlcv",
            ],
            "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
        }

    async def run(
        self,
        *,
        strategy_ids: Sequence[str] | None = None,
        symbols: Sequence[str] | None = None,
        timeframes: Sequence[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        max_symbols: int | None = None,
        taker_fee: float | None = None,
        maker_fee: float | None = None,
        slippage_rate: float | None = None,
        persist: bool = True,
        market_caps: dict[str, float | None] | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        cfg = MultiCapResearchConfig()
        if taker_fee is not None:
            cfg.taker_fee = float(taker_fee)
        if maker_fee is not None:
            cfg.maker_fee = float(maker_fee)
        if slippage_rate is not None:
            cfg.slippage_rate = float(slippage_rate)

        adapter = MultiCapStrategyAdapter(cfg)
        if strategy_ids:
            ids = [s.upper().strip() for s in strategy_ids if s and str(s).strip()]
        else:
            ids = [d["strategy_id"] for d in list_strategy_definitions()]

        tfs = [
            normalize_research_timeframe(tf)
            for tf in (timeframes or list(cfg.primary_timeframes))
        ]

        if symbols:
            syms = [normalize_research_symbol(s) for s in symbols]
        else:
            try:
                syms = await list_symbols_with_ohlcv(tfs[0] if tfs else None)
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "ERROR",
                    "reason": f"OHLCV list failed: {exc}",
                    "disclaimer": DISCLAIMER,
                }

        if max_symbols is not None:
            syms = syms[: int(max_symbols)]

        caps: dict[str, float | None] = {}
        for s in syms:
            key = s.upper()
            if market_caps and key in market_caps:
                caps[key] = market_caps[key]
            else:
                caps[key] = market_cap_from_store(s)

        bounds = resolve_date_bounds(start, end)
        start_dt = bounds.get("start")
        end_excl = bounds.get("end_exclusive")
        series_runs: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        eligibility_reports: dict[str, Any] = {}

        for sid in ids:
            elig = adapter.filter_universe(sid, syms, caps)
            eligibility_reports[sid] = elig.to_dict()
            for tf in tfs:
                for sym in elig.eligible_symbols:
                    try:
                        if start_dt is not None or end_excl is not None:
                            candles = await load_ohlcv_series_range(
                                sym,
                                tf,
                                start=start_dt,
                                end_exclusive=end_excl,
                                warmup_bars=cfg.min_bars,
                            )
                        elif limit is not None:
                            candles = await load_ohlcv_series_tail(
                                sym, tf, limit=int(limit)
                            )
                        else:
                            candles = await load_ohlcv_series_tail(sym, tf, limit=5000)
                        candles, _eval_start, _slice_meta = slice_candles_for_research(
                            candles,
                            start=start_dt,
                            end_exclusive=end_excl,
                            limit=limit,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "multi_cap_load_failed",
                            symbol=sym,
                            timeframe=tf,
                            error=str(exc),
                        )
                        series_runs.append(
                            {
                                "status": "ERROR",
                                "strategy_id": sid,
                                "symbol": sym,
                                "timeframe": tf,
                                "reason": str(exc),
                            }
                        )
                        continue

                    run = run_strategy_on_series(
                        strategy_id=sid,
                        symbol=sym,
                        timeframe=tf,
                        candles=candles,
                        market_cap=caps.get(sym),
                        config=cfg,
                        adapter=adapter,
                    )
                    series_runs.append(run)
                    events.extend(run.get("events") or [])

        results = []
        for sid in ids:
            sid_runs = [r for r in series_runs if r.get("strategy_id") == sid]
            agg = aggregate_runs(sid_runs, strategy_id=sid)
            elig = eligibility_reports.get(sid) or {}
            agg["eligible_symbols"] = elig.get("eligible_symbols") or []
            agg["excluded_symbols"] = elig.get("excluded_symbols") or []
            agg["unknown_symbols"] = elig.get("unknown_symbols") or []
            agg["fee_assumptions"] = {
                "taker_fee": cfg.taker_fee,
                "maker_fee": cfg.maker_fee,
                "slippage_rate": cfg.slippage_rate,
            }
            results.append(agg)

        summary: dict[str, Any] = {
            "status": "OK",
            "run_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration_hash": cfg.configuration_hash(),
            "configuration": cfg.configuration_dict(),
            "research_engine_version": RESEARCH_ENGINE_VERSION,
            "data_period": {
                "start": start,
                "end": end,
                "limit": limit,
                "bounds": {
                    k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in bounds.items()
                    if k not in ("start", "end_exclusive")
                },
            },
            "symbols": syms,
            "timeframes": tfs,
            "strategies_tested": len(ids),
            "results": results,
            "series_runs": series_runs,
            "events": events,
            "eligibility": eligibility_reports,
            "elapsed_seconds": time.perf_counter() - t0,
            "disclaimer": DISCLAIMER,
            "label": "Historical Result",
            "signal_vs_trade_evaluation": {
                "SIGNAL LOGIC": "multi_cap strategy candidate generation",
                "TRADE EVALUATION LOGIC": cfg.trade_evaluation_mechanics,
            },
            "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
            "note": "No strategy ranking / optimization performed.",
        }

        if persist:
            run_id = await save_run(summary)
            summary["run_id"] = run_id
        return summary

    async def get_run(self, run_id: str) -> dict[str, Any]:
        payload = await load_run(run_id)
        if payload is None:
            return {
                "status": "NOT_FOUND",
                "run_id": run_id,
                "disclaimer": DISCLAIMER,
            }
        return {"status": "OK", **payload}


_SERVICE: MultiCapStrategiesService | None = None


def get_multi_cap_strategies_service() -> MultiCapStrategiesService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = MultiCapStrategiesService()
    return _SERVICE
