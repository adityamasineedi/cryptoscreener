"""API facade for multi-cap research strategies.

Research only — never imports into live signal engine paths.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.core.logging import get_logger
from app.research.historical_market_cap.classifier import (
    cap_group_at,
    filter_candidates_by_historical_cap,
)
from app.research.historical_market_cap.repository import load_observations_many
from app.research.multi_cap_strategies.adapter import (
    _GENERATORS,
    MultiCapStrategyAdapter,
    get_strategy,
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
from app.research.multi_cap_strategies.schemas import CapEligibility, StrategyCandidate
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

CAP_MODE_AS_OF = "AS_OF"
CAP_MODE_CURRENT = "CURRENT_CAP"
CAP_MODE_NOTES = {
    CAP_MODE_AS_OF: (
        "Each signal is classified by the stored market cap as of its signal time. "
        "A symbol with no stored cap history returns no signals. "
        "eligible_symbols lists the symbols that have stored cap history."
    ),
    CAP_MODE_CURRENT: (
        "The whole history is classified with one current market cap per symbol."
    ),
}

ObservationsLoader = Callable[
    [Sequence[str]], Awaitable[Mapping[str, Sequence[Mapping[str, Any]]]]
]


class _AsOfCapAdapter(MultiCapStrategyAdapter):
    """Keeps a candidate only when the symbol's cap bucket as of its signal time matches."""

    def __init__(
        self,
        config: MultiCapResearchConfig,
        observations_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        super().__init__(config)
        self.observations_by_symbol = observations_by_symbol
        # Filter statistics of the latest generate_candidates call.
        self.last_cap_stats: dict[str, int] | None = None

    def generate_candidates(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        *,
        market_cap: float | None,
        index_start: int | None = None,
        index_end: int | None = None,
    ) -> tuple[list[StrategyCandidate], dict[str, Any]]:
        definition = get_strategy(strategy_id)
        if definition is None:
            return [], {"status": "ERROR", "reason": f"Unknown strategy {strategy_id}"}
        cands = _GENERATORS[definition.strategy_id].generate_candidates(
            symbol,
            timeframe,
            candles,
            config=self.config,
            index_start=index_start,
            index_end=index_end,
        )
        eligible, _cross, stats = filter_candidates_by_historical_cap(
            cands,
            required_group=definition.asset_group,
            observations_by_symbol=self.observations_by_symbol,
            config=self.config,
        )
        for c in eligible:
            lookup = cap_group_at(
                c.symbol,
                datetime.fromisoformat(str(c.signal_time).replace("Z", "+00:00")),
                self.observations_by_symbol.get(c.symbol.upper()) or [],
                config=self.config,
            )
            c.metadata["cap_as_of"] = lookup.to_dict()
        self.last_cap_stats = stats
        return eligible, {
            "status": "OK",
            "strategy_id": definition.strategy_id,
            "symbol": symbol.upper(),
            "resolved_group": CAP_MODE_AS_OF,
            "required_group": definition.asset_group,
            "reason": "OK",
            "signal_logic": definition.strategy_id,
            "trade_evaluation_logic": "combination_backtest.evaluate_candidate_trades",
            "signal_count": len(eligible),
        }

    def candidates_to_trades(
        self,
        candidates: Sequence[StrategyCandidate],
        *,
        asset_group: str,
        period_label: str = "FULL",
    ) -> list[Any]:
        trades: list[Any] = []
        for c in candidates:
            trades.extend(
                super().candidates_to_trades(
                    [c],
                    asset_group=c.metadata["cap_as_of"]["cap_group"],
                    period_label=period_label,
                )
            )
        return trades


def _cap_history_eligibility(
    strategy_id: str,
    symbols: Sequence[str],
    observations_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
) -> CapEligibility:
    """Symbols with stored cap history are run; the rest are excluded for missing cap."""
    definition = get_strategy(strategy_id)
    if definition is None:
        return CapEligibility(
            excluded_symbols=[{"symbol": s, "reason": "UNKNOWN_STRATEGY"} for s in symbols]
        )
    elig = CapEligibility()
    for sym in symbols:
        key = sym.upper()
        if observations_by_symbol.get(key):
            elig.eligible_symbols.append(key)
            continue
        row = {
            "symbol": key,
            "resolved_group": "CAP_GROUP_UNAVAILABLE",
            "required_group": definition.asset_group,
            "reason": "CAP_GROUP_UNAVAILABLE",
            "detail": "NO_STORED_CAP_HISTORY",
            "market_cap": None,
        }
        elig.unknown_symbols.append(row)
        elig.excluded_symbols.append(row)
    return elig


class MultiCapStrategiesService:
    def __init__(self, *, observations_loader: ObservationsLoader | None = None) -> None:
        # Stored cap observations per symbol. Tests inject a fake.
        self._load_observations = observations_loader or load_observations_many

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
        cap_mode: str | None = None,
    ) -> dict[str, Any]:
        """Run the strategies.

        AS_OF (default): each signal is gated by the stored cap as of its signal time.
        CURRENT_CAP (``market_caps`` supplied, or ``cap_mode="current"``): one current
        cap per symbol classifies the whole history.
        """
        t0 = time.perf_counter()
        requested_mode = str(cap_mode or "").upper().strip()
        if requested_mode in ("CURRENT", CAP_MODE_CURRENT):
            mode = CAP_MODE_CURRENT
        elif requested_mode == CAP_MODE_AS_OF:
            mode = CAP_MODE_AS_OF
        elif not requested_mode:
            mode = CAP_MODE_CURRENT if market_caps else CAP_MODE_AS_OF
        else:
            return {
                "status": "ERROR",
                "reason": f"Unknown cap_mode {cap_mode}",
                "disclaimer": DISCLAIMER,
            }
        if mode == CAP_MODE_AS_OF and market_caps:
            return {
                "status": "ERROR",
                "reason": "market_caps applies to CURRENT_CAP mode only",
                "disclaimer": DISCLAIMER,
            }
        as_of = mode == CAP_MODE_AS_OF
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
        observations: dict[str, list[Mapping[str, Any]]] = {}
        as_of_adapter: _AsOfCapAdapter | None = None
        if as_of:
            # No current cap is read in this mode.
            try:
                loaded = await self._load_observations(syms)
            except Exception as exc:  # noqa: BLE001
                return {
                    "status": "ERROR",
                    "reason": f"Market-cap history load failed: {exc}",
                    "cap_mode": mode,
                    "disclaimer": DISCLAIMER,
                }
            observations = {str(k).upper(): list(v or []) for k, v in loaded.items()}
            as_of_adapter = _AsOfCapAdapter(cfg, observations)
        else:
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
        as_of_cap_filter: dict[str, Any] = {}

        for sid in ids:
            if as_of:
                elig = _cap_history_eligibility(sid, syms, observations)
                definition = get_strategy(sid)
                as_of_cap_filter[sid] = {
                    "required_group": definition.asset_group if definition else None,
                    "totals": {},
                    "by_symbol": {},
                    "symbols_excluded_missing_cap_history": [
                        row["symbol"] for row in elig.unknown_symbols
                    ],
                }
            else:
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

                    if as_of_adapter is not None:
                        as_of_adapter.last_cap_stats = None
                    run = run_strategy_on_series(
                        strategy_id=sid,
                        symbol=sym,
                        timeframe=tf,
                        candles=candles,
                        market_cap=caps.get(sym),
                        config=cfg,
                        adapter=as_of_adapter or adapter,
                    )
                    if as_of_adapter is not None:
                        # Buckets are per signal: see each trade's asset_group.
                        run["cap_mode"] = mode
                        run["resolved_cap_group"] = "AS_OF_PER_SIGNAL"
                        stats = as_of_adapter.last_cap_stats
                        if stats is not None:
                            run["as_of_cap_filter"] = dict(stats)
                            report = as_of_cap_filter[sid]
                            report["by_symbol"].setdefault(sym, {})[tf] = dict(stats)
                            for name, count in stats.items():
                                report["totals"][name] = report["totals"].get(name, 0) + count
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
            "cap_mode": mode,
            "cap_mode_note": CAP_MODE_NOTES[mode],
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

        if as_of:
            summary["as_of_cap_filter"] = as_of_cap_filter

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
