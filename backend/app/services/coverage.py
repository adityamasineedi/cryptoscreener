from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.engines.orchestrator import get_orchestrator
from app.models.schemas import DataStatus, FreshValue
from app.services.engine_store import engine_store
from app.services.freshness import classify_freshness
from app.services.historical_performance import compute_performance_sync
from app.services.market_store import market_store
from app.services.metric_dependencies import build_metric_dependency_diagnostics
from app.services.ohlcv_store import ohlcv_store
from app.services.volume_change import compute_volume_changes
from app.services.asset_metadata import asset_registry

def _count_fresh(
    items: list[FreshValue | None],
    *,
    stale_after: float,
    unavailable_after: float,
) -> dict[str, int]:
    out = {
        "available": 0,
        "live": 0,
        "historical": 0,
        "cached": 0,
        "stale": 0,
        "waiting": 0,
        "unavailable": 0,
    }
    for fv in items:
        if fv is None:
            out["waiting"] += 1
            continue
        if fv.value is None and fv.status == DataStatus.WAITING:
            out["waiting"] += 1
            continue
        if fv.value is None and fv.status == DataStatus.UNAVAILABLE:
            out["unavailable"] += 1
            continue
        if fv.status == DataStatus.HISTORICAL and fv.value is not None:
            status = DataStatus.HISTORICAL
        elif fv.timestamp is not None:
            status = classify_freshness(
                fv.timestamp,
                stale_after=stale_after,
                unavailable_after=unavailable_after,
                currently_live=fv.status == DataStatus.LIVE,
            )
            if fv.status == DataStatus.CACHED and status == DataStatus.LIVE:
                status = DataStatus.CACHED
            if fv.status == DataStatus.WAITING and fv.value is None:
                status = DataStatus.WAITING
        else:
            status = fv.status
        key = status.value.lower()
        out[key] = out.get(key, 0) + 1
        if status in (
            DataStatus.LIVE,
            DataStatus.HISTORICAL,
            DataStatus.CACHED,
            DataStatus.STALE,
        ) and fv.value is not None:
            out["available"] += 1
    return out

def _ohlcv_tf_coverage(symbols: list[str], timeframe: str) -> dict[str, int]:
    covered = 0
    waiting = 0
    for sym in symbols:
        candles = ohlcv_store.get_closed(sym, timeframe)
        if candles:
            covered += 1
        else:
            waiting += 1
    return {
        "available": covered,
        "live": covered,
        "stale": 0,
        "unavailable": 0,
        "waiting": waiting,
        "covered": covered,
        "total": len(symbols),
    }

async def build_data_coverage(settings) -> dict[str, Any]:
    symbols = [s.symbol for s in market_store.list_symbols(market_type="futures_perp")]
    stale = float(settings.stale_ticker_seconds)
    unavail = float(settings.unavailable_after_seconds)
    fund_stale = float(settings.stale_fundamental_seconds)
    orch = get_orchestrator()
    ticker_fvs: list[FreshValue | None] = []
    for sym in symbols:
        t = market_store.tickers.get(sym)
        if t is None:
            ticker_fvs.append(None)
        else:
            ticker_fvs.append(
                FreshValue(
                    value=t.price,
                    timestamp=t.timestamp,
                    source=t.source,
                    status=t.status,
                )
            )
    oi_fvs: list[FreshValue | None] = []
    liq_fvs: list[FreshValue | None] = []
    for sym in symbols:
        if orch is None:
            oi_fvs.append(None)
            liq_fvs.append(None)
            continue
        st = orch.oi.get_state(sym)
        oi_fvs.append(st.open_interest if st else None)
        liq_fvs.append(orch.liquidations.get_summary(sym))
    mcap_fvs = [engine_store.get_fundamental(s, "market_cap") for s in symbols]
    fdv_fvs = [engine_store.get_fundamental(s, "fdv") for s in symbols]
    tvl_fvs = [engine_store.get_fundamental(s, "tvl") for s in symbols]
    rank_fvs = [engine_store.get_fundamental(s, "market_rank") for s in symbols]
    # Sample performance / volume-change / on-chain / sentiment coverage
    perf_1d: list[FreshValue | None] = []
    vol_24h_chg: list[FreshValue | None] = []
    holder_fvs: list[FreshValue | None] = []
    tx_fvs: list[FreshValue | None] = []
    sent_fvs: list[FreshValue | None] = []
    mapped = 0
    for sym in symbols:
        if asset_registry.get_for_symbol(sym) is not None:
            mapped += 1
        perf = compute_performance_sync(sym)
        perf_1d.append(perf.get("performance_1d"))
        vchg = compute_volume_changes(sym)
        vol_24h_chg.append(vchg.get("volume_change_24h"))
        # On-chain / sentiment — use cached engine values when present; else WAITING
        holder_fvs.append(
            FreshValue.waiting("onchain")
            if asset_registry.get_for_symbol(sym) is None
            else FreshValue.waiting("onchain")
        )
        tx_fvs.append(FreshValue.waiting("onchain"))
        sent = engine_store.sentiment.get(sym) or {}
        sent_fvs.append(
            sent.get("sentiment")
            or sent.get("social_dominance")
            or FreshValue.waiting("sentiment")
        )
    tfs = settings.market_config.get("timeframes") or ["1m", "5m", "15m", "1h", "4h", "1d"]
    ohlcv = {
        str(tf).lower() if str(tf) != "1D" else "1d": _ohlcv_tf_coverage(
            symbols, str(tf).lower() if str(tf) != "1D" else "1d"
        )
        for tf in tfs
    }
    payload = {
        "symbols": len(symbols),
        "ticker": _count_fresh(ticker_fvs, stale_after=stale, unavailable_after=unavail),
        "ohlcv": ohlcv,
        "open_interest": _count_fresh(
            oi_fvs,
            stale_after=float(
                (settings.market_config.get("open_interest") or {}).get("stale_seconds", 300)
            ),
            unavailable_after=unavail * 10,
        ),
        "liquidations": _count_fresh(
            liq_fvs, stale_after=stale * 4, unavailable_after=unavail * 10
        ),
        "market_cap": _count_fresh(
            mcap_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4
        ),
        "fdv": _count_fresh(fdv_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4),
        "tvl": _count_fresh(tvl_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4),
        "market_rank": _count_fresh(
            rank_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4
        ),
        "historical_performance": {
            "performance_1d": _count_fresh(
                perf_1d, stale_after=86400, unavailable_after=86400 * 3
            ),
        },
        "volume_change": {
            "volume_change_24h": _count_fresh(
                vol_24h_chg, stale_after=3600, unavailable_after=86400
            ),
        },
        "holders": _count_fresh(holder_fvs, stale_after=3600, unavailable_after=86400),
        "transactions": _count_fresh(tx_fvs, stale_after=3600, unavailable_after=86400),
        "sentiment": _count_fresh(sent_fvs, stale_after=600, unavailable_after=3600),
        "asset_mappings": {
            "mapped_symbols": mapped,
            "waiting_unmapped": len(symbols) - mapped,
            **asset_registry.stats(),
        },
        "fundamentals": {
            "market_cap": _count_fresh(
                mcap_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4
            ),
            "fdv": _count_fresh(
                fdv_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4
            ),
            "tvl": _count_fresh(
                tvl_fvs, stale_after=fund_stale, unavailable_after=fund_stale * 4
            ),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": "available/waiting/stale/unavailable counts — incomplete coverage is not hidden",
        # Screen display universe is separate from full backend ingestion universe
        "screen": {
            "max_screen_symbols": 100,
            "note": (
                "Main screener shows ≤100 dynamically selected symbols. "
                "This Data Health page reports the full backend universe."
            ),
        },
    }
    payload = _attach_pct(payload, len(symbols))
    payload["coverage_goals"] = _coverage_goals(payload, orch)
    payload["metric_dependencies"] = build_metric_dependency_diagnostics(symbols, orch=orch)
    if orch is not None and getattr(orch, "fundamentals", None) is not None:
        payload["fundamental_reasons"] = orch.fundamentals.coverage_reasons()
    return payload


def _coverage_goals(payload: dict[str, Any], orch) -> dict[str, Any]:
    """Explicit measured goals — never mark complete without evidence."""
    goals = {
        "1d": 95.0,
        "4h": 95.0,
        "1h": 95.0,
        "15m": 95.0,
        "5m": 95.0,
        "1m": "rolling",
        "oi": 95.0,
    }
    ohlcv = payload.get("ohlcv") or {}
    out: dict[str, Any] = {}
    met_all = True
    for tf, goal in goals.items():
        if tf == "oi":
            oi = payload.get("open_interest") or {}
            pct = float(oi.get("pct_available") or 0.0)
            met = pct >= float(goal)
            if not met:
                met_all = False
            out["oi"] = {"goal_pct": float(goal), "pct": pct, "met": met}
            continue
        if goal == "rolling":
            block = ohlcv.get("1m") or {}
            out["1m"] = {
                "goal": "rolling",
                "pct": float(block.get("pct_available") or 0.0),
                "covered": block.get("available") or block.get("covered") or 0,
                "met": None,
                "note": "rolling top-volume + visible universe",
            }
            continue
        block = ohlcv.get(tf) or {}
        pct = float(block.get("pct_available") or 0.0)
        met = pct >= float(goal)
        if not met:
            met_all = False
        out[tf] = {
            "goal_pct": float(goal),
            "pct": pct,
            "covered": block.get("available") or block.get("covered") or 0,
            "total": block.get("total"),
            "met": met,
        }
    if orch is not None and getattr(orch, "backfill", None) is not None:
        # Prefer backfill's 1m rolling universe measurement when available
        try:
            from app.services.market_store import market_store as ms

            symbols = [s.symbol for s in ms.list_symbols(market_type="futures_perp")]
            bf_goals = orch.backfill.coverage_goals_status(symbols)
            if "1m" in (bf_goals.get("targets") or {}):
                out["1m"] = bf_goals["targets"]["1m"]
        except Exception:  # noqa: BLE001
            pass
    return {
        "targets": out,
        "all_measured_targets_met": met_all,
        "system_complete": False if not met_all else met_all,
        "note": "Do not report COMPLETE until measured coverage targets are achieved",
    }


def _attach_pct(payload: dict[str, Any], total: int) -> dict[str, Any]:
    """Add pct_available to FreshValue count blocks and ohlcv TF maps."""

    def pct_block(block: dict[str, Any]) -> dict[str, Any]:
        avail = int(block.get("available") or block.get("covered") or 0)
        return {
            **block,
            "total": block.get("total", total),
            "pct_available": round(100.0 * avail / total, 2) if total else 0.0,
        }

    out = dict(payload)
    for key in (
        "ticker",
        "open_interest",
        "liquidations",
        "market_cap",
        "fdv",
        "tvl",
        "market_rank",
        "holders",
        "transactions",
        "sentiment",
    ):
        if isinstance(out.get(key), dict):
            out[key] = pct_block(out[key])
    if isinstance(out.get("ohlcv"), dict):
        out["ohlcv"] = {
            tf: pct_block(v) if isinstance(v, dict) else v
            for tf, v in out["ohlcv"].items()
        }
    for nest in ("fundamentals", "historical_performance", "volume_change"):
        if isinstance(out.get(nest), dict):
            out[nest] = {
                k: pct_block(v) if isinstance(v, dict) else v
                for k, v in out[nest].items()
            }
    return out


async def build_ohlcv_coverage(settings) -> dict[str, Any]:
    symbols = [s.symbol for s in market_store.list_symbols(market_type="futures_perp")]
    orch = get_orchestrator()
    if orch is not None and getattr(orch, "backfill", None) is not None:
        return orch.backfill.coverage_snapshot(symbols)
    tfs = settings.market_config.get("timeframes") or ["1m", "5m", "15m", "1h", "4h", "1d"]
    by_tf = {}
    oldest = newest = None
    missing = 0
    for tf in tfs:
        key = str(tf).lower() if str(tf) != "1D" else "1d"
        covered = 0
        for sym in symbols:
            candles = ohlcv_store.get_closed(sym, key)
            if candles:
                covered += 1
                if oldest is None or candles[0].open_time < oldest:
                    oldest = candles[0].open_time
                if newest is None or candles[-1].open_time > newest:
                    newest = candles[-1].open_time
                missing += len(ohlcv_store.find_gaps(sym, key)[:20])
        by_tf[key] = {
            "covered": covered,
            "total": len(symbols),
            "pct": round(100.0 * covered / len(symbols), 2) if symbols else 0.0,
        }
    return {
        "symbol_count": len(symbols),
        "coverage_by_timeframe": by_tf,
        "oldest_candle": oldest.isoformat() if oldest else None,
        "newest_candle": newest.isoformat() if newest else None,
        "missing_ranges": missing,
        "backfill_queue_size": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
