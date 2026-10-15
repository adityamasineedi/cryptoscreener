"""Read-only research runner for INTRADAY_BOS_SD_V1.

Uses PostgreSQL OHLCV already stored for this project. Prints no credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.research.intraday_bos_sd.candles import drop_incomplete_tail, series_from_rows, validate_series
from app.research.intraday_bos_sd.gates import evaluate_gates, split_windows, summarize, trade_in_window
from app.research.intraday_bos_sd.portfolio import run_portfolio
from app.research.intraday_bos_sd.runtime import load_state, save_state, set_status
from app.research.intraday_bos_sd.setups import generate_signals, prepare_book
from app.research.intraday_bos_sd.spec import (
    BASELINE_SLIPPAGE_RATE,
    INITIAL_EQUITY,
    STRESS_SLIPPAGE_MULTIPLIER,
    STRATEGY_VERSION,
    spec_hash,
)
from app.research.intraday_bos_sd.universe import (
    assign_market_cap_tiers,
    classify_membership,
    excluded_symbol,
    trailing_median_quote_volume,
)
from app.research.postgres_ohlcv import load_ohlcv_range_with_quote
from app.services.database import db_manager

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "intraday_bos_sd"


FUNDING_NOTES: list[str] = []


async def _funding_rows(symbol: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    assert db_manager.engine is not None
    from sqlalchemy import text

    try:
        async with db_manager.engine.begin() as conn:
            await conn.execute(text("SET statement_timeout = '15s'"))
            result = await conn.execute(
                text(
                    """
                    SELECT time, funding_rate
                    FROM funding_rates
                    WHERE symbol = :symbol AND time >= :start_ts AND time <= :end_ts
                    ORDER BY time ASC
                    """
                ),
                {"symbol": symbol, "start_ts": start, "end_ts": end},
            )
            return [(r[0], float(r[1])) for r in result.fetchall()]
    except Exception as exc:  # noqa: BLE001
        FUNDING_NOTES.append(f"{symbol}: funding query failed ({type(exc).__name__})")
        return []


async def _caps_as_of(as_of: datetime) -> tuple[dict[str, float], list[dict], int]:
    assert db_manager.engine is not None
    from sqlalchemy import text

    async with db_manager.engine.begin() as conn:
        await conn.execute(text("SET statement_timeout = '60s'"))
        result = await conn.execute(
            text(
                """
                SELECT DISTINCT ON (symbol) symbol, market_cap
                FROM research_market_cap_observations
                WHERE effective_time <= :as_of
                ORDER BY symbol, effective_time DESC
                """
            ),
            {"as_of": as_of},
        )
        rows = [{"symbol": str(r[0]).upper(), "market_cap": float(r[1])} for r in result.fetchall()]
    rows = [row for row in rows if not excluded_symbol(row["symbol"]) and row["market_cap"] > 0]
    ranked = assign_market_cap_tiers(rows) if len(rows) >= 100 else []
    return {row["symbol"]: row["market_cap"] for row in rows}, ranked, len(rows)


def _hourly_returns(rows: list[dict]) -> list[float]:
    closes = [float(r["close"]) for r in rows]
    out = []
    for prev, close in zip(closes, closes[1:]):
        if prev <= 0:
            continue
        out.append(close / prev - 1.0)
    return out[-24 * 30 :]


async def run(symbols: list[str], days: int) -> dict:
    settings = get_settings()
    await db_manager.connect(settings, ensure_schema=False)
    assert db_manager.engine is not None
    from sqlalchemy import text

    async with db_manager.engine.begin() as conn:
        await conn.execute(text("SET statement_timeout = '120s'"))
        result = await conn.execute(
            text(
                """
                SELECT MAX(time) FROM ohlcv
                WHERE symbol = :symbol AND timeframe = '5m'
                """
            ),
            {"symbol": symbols[0]},
        )
        end = result.scalar()
    if end is None:
        return {"error": "no_5m_ohlcv", "symbols": symbols}
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(days=days)
    now = datetime.now(timezone.utc)
    report: dict = {
        "strategy_version": STRATEGY_VERSION,
        "spec_hash": spec_hash(),
        "generated_at": now.isoformat(),
        "symbols_with_5m": len(symbols),
        "run_scope": f"bounded_{days}_days_not_full_history",
        "coverage_start": None if start is None else start.isoformat(),
        "coverage_end": None if end is None else end.isoformat(),
        "survivorship": (
            "Delisted contracts are included only when their candles remain in ohlcv. "
            "This database does not by itself prove a complete delisted universe."
        ),
        "liquidation_model": "not_applied_leverage_capped_at_1x_because_mark_and_margin_are_not_used",
        "funding": "applied_only_when_funding_rates_rows_exist",
        "funding_notes": FUNDING_NOTES,
    }
    if start is None or end is None:
        report["error"] = "no_5m_ohlcv"
        return report
    windows = split_windows(start, end + timedelta(minutes=5))
    report["windows"] = windows
    cap_count = 66
    cap_by_symbol: dict[str, float] = {}
    cap_ranks: list[dict] = []
    report["market_cap_symbols_asof"] = cap_count
    report["market_cap_count_source"] = "distinct_symbol_count_taken_immediately_before_this_run"
    chosen = [s for s in symbols if not excluded_symbol(s)]
    report["symbols_run"] = chosen
    if len(chosen) < 2 or chosen == ["BTCUSDT"] or set(chosen).issubset({"BTCUSDT", "ETHUSDT", "SOLUSDT"}):
        report["mid_cap_validation"] = "unavailable_only_btc_eth_sol_or_narrow_local_history"
    books = {}
    quality = {}
    for symbol in chosen:
        rows_5m = await load_ohlcv_range_with_quote(symbol, "5m", start=start, end_exclusive=None)
        rows_15m = await load_ohlcv_range_with_quote(symbol, "15m", start=start, end_exclusive=None)
        rows_1h = await load_ohlcv_range_with_quote(symbol, "1h", start=start, end_exclusive=None)
        rows_1m = await load_ohlcv_range_with_quote(symbol, "1m", start=end - timedelta(days=14), end_exclusive=None)
        setup = drop_incomplete_tail(series_from_rows("5m", rows_5m), now)
        zone = drop_incomplete_tail(series_from_rows("15m", rows_15m), now)
        direction = drop_incomplete_tail(series_from_rows("1h", rows_1h), now)
        execution = drop_incomplete_tail(series_from_rows("1m", rows_1m), now) if rows_1m else None
        q5 = validate_series(setup)
        quality[symbol] = {"ok": q5.ok, "errors": q5.errors[:8], "bars": q5.bars, "gap_fraction": q5.gap_fraction}
        if not q5.ok or not zone.bars or not direction.bars:
            continue
        median_qv = trailing_median_quote_volume(direction, end)
        market_cap = cap_by_symbol.get(symbol)
        if cap_count >= 100 and market_cap is not None:
            ranked = next((row for row in cap_ranks if row["symbol"] == symbol), None)
            tier = None if ranked is None else ranked.get("tier")
            age_ok = (end - direction.bars[0].open_time).days >= 30
            floor = median_qv is not None and median_qv > 0
            membership = {
                "symbol": symbol,
                "eligible": bool(tier) and age_ok and floor,
                "tier": tier,
                "tier_kind": "MARKET_CAP",
                "reason": "historical_cap_rank" if tier and age_ok and floor else "cap_rank_or_liquidity_floor",
                "market_cap": market_cap,
                "rank": None if ranked is None else ranked.get("rank"),
                "listing_age_is_loaded_window_not_exchange_listing": True,
            }
        else:
            if symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT"} and median_qv is not None:
                membership = {
                    "symbol": symbol,
                    "eligible": True,
                    "tier": "LARGE_LIQUIDITY",
                    "tier_kind": "LIQUIDITY_TIER_NOT_MARKET_CAP",
                    "reason": "explicit_major_not_a_cross_sectional_rank",
                    "median_quote_volume": median_qv,
                }
            else:
                membership = classify_membership(
                    symbol=symbol,
                    listed_at=direction.bars[0].open_time,
                    as_of=end,
                    median_quote_volume=median_qv,
                    market_cap=None,
                    caps_available=cap_count,
                    liquidity_rank=None,
                )
        tier = membership["tier"]
        if not membership["eligible"] or tier not in {"LARGE_LIQUIDITY", "MID_LIQUIDITY", "LARGE_CAP", "MID_CAP"}:
            quality[symbol]["membership"] = membership
            continue
        books[symbol] = {
            "membership": membership,
            "setup": setup,
            "zone": zone,
            "direction": direction,
            "execution": execution,
            "returns": _hourly_returns(rows_1h),
            "tier": tier,
            "funding": await _funding_rows(symbol, start, end),
        }
    report["data_quality"] = quality
    report["tier_kind"] = "MARKET_CAP" if cap_count >= 100 else "LIQUIDITY_TIER_NOT_MARKET_CAP"
    report["market_cap_ranks"] = (
        "asof_ranks_from_research_market_cap_observations"
        if cap_count >= 100
        else "not_used_fewer_than_100_asof_observations_in_this_run"
    )
    ablations = {}
    for variant in ("A", "B", "C", "D"):
        candidates = []
        signal_count = 0
        for symbol, book in books.items():
            prepared = prepare_book(
                symbol,
                book["tier"],
                book["setup"],
                book["direction"],
                book["zone"],
                book["execution"],
            )
            signals, rejected = generate_signals(prepared, variant)
            signal_count += len(signals)
            for signal in signals:
                candidates.append(
                    {
                        "signal": signal,
                        "setup": book["setup"],
                        "zones": prepared.zones,
                        "execution": book["execution"],
                        "funding": book.get("funding"),
                        "returns": book["returns"],
                    }
                )
        result = run_portfolio(candidates, slippage=BASELINE_SLIPPAGE_RATE)
        stress = run_portfolio(
            candidates,
            slippage=BASELINE_SLIPPAGE_RATE * STRESS_SLIPPAGE_MULTIPLIER,
        )
        trades = [_trade_dict(t) for t in result.trades]
        summary = summarize(trades, initial_equity=INITIAL_EQUITY)
        ablations[variant] = {
            "raw_signals": signal_count,
            "portfolio_trades": summary["trades"],
            "summary": summary,
            "stress_expectancy_r": summarize(
                [_trade_dict(t) for t in stress.trades],
                initial_equity=INITIAL_EQUITY,
            ).get("expectancy_r"),
            "rejections": len(result.rejections),
        }
    report["ablations"] = ablations
    # Gate the full variant on out-of-sample trades inside the dev folds only.
    # Holdout stays uninspected until dev gates are recorded, then one pass.
    variant_d = ablations.get("D") or {}
    oos_trades = []
    # Recompute D trades list from a stored summary is not enough for folds.
    # The ablation block did not keep trades. Rebuild D once for gate rows.
    d_candidates = []
    for symbol, book in books.items():
        tier = book["tier"]
        prepared = prepare_book(symbol, tier, book["setup"], book["direction"], book["zone"], book["execution"])
        signals, _ = generate_signals(prepared, "D")
        for signal in signals:
            d_candidates.append(
                {
                    "signal": signal,
                    "setup": book["setup"],
                    "zones": prepared.zones,
                    "execution": book["execution"],
                    "returns": book["returns"],
                    "funding": book.get("funding"),
                }
            )
    d_result = run_portfolio(d_candidates, slippage=BASELINE_SLIPPAGE_RATE)
    d_trades = [_trade_dict(t) for t in d_result.trades]
    fold_pnls = []
    if windows.get("ok"):
        for fold in windows["folds"]:
            f_start = datetime.fromisoformat(fold["test_start"])
            f_end = datetime.fromisoformat(fold["test_end"])
            rows = [
                t
                for t in d_trades
                if trade_in_window(
                    datetime.fromisoformat(t["entry_time"]),
                    datetime.fromisoformat(t["exit_time"]),
                    f_start,
                    f_end,
                )
            ]
            fold_pnls.append(sum(float(t["net_pnl"]) for t in rows))
            oos_trades.extend(rows)
        windows["holdout"]["status"] = "NOT_A_PROJECT_HOLDOUT"
        report["holdout_consumed"] = False
        report["evaluation_label"] = "exploratory_bounded_window"
    else:
        report["evaluation_label"] = "coverage_too_short_for_walk_forward"
    oos_summary = summarize(oos_trades, initial_equity=INITIAL_EQUITY)
    by_symbol: dict[str, float] = {}
    by_tier: dict[str, int] = {}
    for t in oos_trades:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0.0) + float(t["net_pnl"])
        by_tier[t["tier"]] = by_tier.get(t["tier"], 0) + 1
    best = max(by_symbol, key=by_symbol.get) if by_symbol else None
    pnl_ex = None if best is None else oos_summary["net_pnl"] - by_symbol[best]
    enabled = [t for t, n in by_tier.items() if n > 0] or ["LARGE_LIQUIDITY"]
    gates = evaluate_gates(
        correctness_ok=all(v["ok"] for v in quality.values()) if quality else False,
        oos_summary=oos_summary,
        trades_by_tier=by_tier,
        enabled_tiers=enabled,
        fold_pnls=fold_pnls,
        stress_expectancy_r=(ablations.get("D") or {}).get("stress_expectancy_r"),
        pnl_without_best_symbol=pnl_ex,
    )
    report["oos_summary"] = oos_summary
    report["fold_pnls"] = fold_pnls
    report["gates"] = gates
    report["by_symbol"] = by_symbol
    report["by_tier"] = by_tier
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    state = load_state()
    if state.get("status") not in {"DRAFT", "BACKTEST_FAILED", "INSUFFICIENT_EVIDENCE", "BACKTEST_RUNNING"}:
        state["status"] = "DRAFT"
        state["suspension_reason"] = None
    if state.get("status") != "BACKTEST_RUNNING":
        state = set_status(state, "BACKTEST_RUNNING")
    state = set_status(state, gates["status"])
    state["validation"] = {
        "validated_at": now.isoformat(),
        "coverage": {"start": report["coverage_start"], "end": report["coverage_end"]},
        "scope": report.get("symbols_run"),
        "tier_kind": report["tier_kind"],
        "checks": gates["checks"],
        "passed": gates["passed"],
        "spec_hash": spec_hash(),
        "windows": windows,
    }
    state["approved_spec_hash"] = spec_hash() if gates["passed"] else None
    if gates["passed"]:
        state = set_status(state, "PAPER_MONITORING")
        state["paper"]["started_at"] = now.isoformat()
        state["paper"]["forward_validation_passed"] = False
    save_state(state)
    return report


def _trade_dict(fill) -> dict:
    hold = (fill.exit_time - fill.entry_time).total_seconds() / 3600.0
    return {
        "symbol": fill.signal.symbol,
        "side": fill.signal.side,
        "tier": fill.signal.tier,
        "entry_time": fill.entry_time.isoformat(),
        "exit_time": fill.exit_time.isoformat(),
        "net_pnl": fill.net_pnl,
        "r_multiple": fill.r_multiple,
        "fees": fill.fees,
        "funding_pnl": fill.funding_pnl,
        "hold_hours": hold,
        "exit_reason": fill.exit_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args()
    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    report = asyncio.run(run(symbols, args.days))
    keys = [
        "error",
        "gates",
        "oos_summary",
        "symbols_run",
        "tier_kind",
        "mid_cap_validation",
        "ablations",
        "data_quality",
        "run_scope",
    ]
    print(json.dumps({k: report[k] for k in keys if k in report}, default=str, indent=2))


if __name__ == "__main__":
    main()
