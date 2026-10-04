"""Research-only SHORT ablation matrix. Never creates paper trades or changes v1."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import (
    run_combination_backtest,
    simulate_research_trade,
)
from app.research.schemas import ResearchTrade
from app.research.combo02_candidate_research import compute_trade_metrics
from app.research.short_research_diagnostics.constants import (
    REGIME_STRONG_BEAR,
    SAFETY_STAMPS,
    VARIANT_BASELINE,
    VARIANT_NO_HTF,
    VARIANT_RETEST_ONLY,
    VARIANT_SPECS,
    VARIANT_STRONG_BEAR,
    VARIANT_SUPPORT_TP,
    VARIANT_WIDER_STOP,
)
from app.research.short_research_diagnostics.metrics import initial_risk
from app.research.short_research_forensics import (
    apply_independent_replay_to_trade,
    audit_trades_for_lookahead,
    enrich_trade_forensic_fields,
)
from app.research.trade_fees import enrich_trades
from app.signals.config import SignalConfig


def variant_fingerprint(variant_id: str, *, window: Mapping[str, Any], symbols: Sequence[str]) -> str:
    spec = VARIANT_SPECS[variant_id]
    payload = {
        "diagnostic": "COMBO_02_SHORT_ABLATION",
        "variant_id": variant_id,
        "spec": spec,
        "window": dict(window),
        "symbols": sorted(str(s).upper() for s in symbols),
        **SAFETY_STAMPS,
    }
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def summarize_variant_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float = 20.0,
) -> dict[str, Any]:
    # Prefer already-computed diagnostic PnL fields when present (filter variants).
    prepared: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        if row.get("exit_price") is None:
            oc = str(row.get("outcome") or "").upper()
            if oc in ("SL", "STOP"):
                row["exit_price"] = row.get("stop_price")
            elif oc.startswith("TP"):
                row["exit_price"] = row.get("TP1") or row.get("tp1") or row.get("take_profit_price")
        if row.get("r_net") is None and row.get("R") is not None:
            row["r_net"] = row.get("R")
        if row.get("gross_pnl_usd") is None and row.get("gross_pnl") is not None:
            row["gross_pnl_usd"] = row.get("gross_pnl")
        if row.get("net_pnl_usd") is None and row.get("net_pnl") is not None:
            row["net_pnl_usd"] = row.get("net_pnl")
        if row.get("fee_total_usd") is None and row.get("fees") is not None:
            row["fee_total_usd"] = row.get("fees")
        prepared.append(row)

    # If rows already carry net/gross, aggregate directly to avoid fee reprice drift.
    if prepared and all(p.get("net_pnl") is not None for p in prepared):
        rs = [float(p["R"] if p.get("R") is not None else p.get("r_net") or 0) for p in prepared]
        nets = [float(p["net_pnl"]) for p in prepared]
        grosses = [float(p.get("gross_pnl") or 0) for p in prepared]
        fees = [float(p.get("fees") or 0) for p in prepared]
        wins = sum(1 for r in rs if r > 0)
        gains = sum(r for r in rs if r > 0)
        losses = sum(abs(r) for r in rs if r < 0)
        # drawdown on R series
        peak = 0.0
        dd = 0.0
        eq = 0.0
        streak = 0
        max_streak = 0
        for r in rs:
            eq += r
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            if r < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        mfes = [float(p["MFE"]) for p in prepared if p.get("MFE") is not None]
        maes = [float(p["MAE"]) for p in prepared if p.get("MAE") is not None]
        if not mfes:
            mfes = [float(p["mfe"]) for p in prepared if p.get("mfe") is not None]
        if not maes:
            maes = [float(p["mae"]) for p in prepared if p.get("mae") is not None]
        regimes: dict[str, int] = {}
        for t in prepared:
            for r in t.get("regimes") or (
                [t.get("primary_regime")] if t.get("primary_regime") else []
            ):
                if r:
                    regimes[str(r)] = regimes.get(str(r), 0) + 1
        return {
            "trade_count": len(prepared),
            "win_rate": (wins / len(rs)) if rs else None,
            "gross_pnl": sum(grosses),
            "fees": sum(fees),
            "net_pnl": sum(nets),
            "average_net_r": (sum(rs) / len(rs)) if rs else None,
            "profit_factor": (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
            "maximum_drawdown": dd,
            "maximum_losing_streak": max_streak,
            "avg_mfe": (sum(mfes) / len(mfes)) if mfes else None,
            "avg_mae": (sum(maes) / len(maes)) if maes else None,
            "regime_distribution": regimes,
            "closed_trades": prepared,
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
        }

    enriched = enrich_trades(prepared, risk_usd=risk_usd, closed_only=True)
    metrics = compute_trade_metrics(enriched)
    mfes = [float(t["mfe"]) for t in enriched if t.get("mfe") is not None]
    maes = [float(t["mae"]) for t in enriched if t.get("mae") is not None]
    regimes = {}
    for t in enriched:
        for r in t.get("regimes") or ([t.get("primary_regime")] if t.get("primary_regime") else []):
            if r:
                regimes[str(r)] = regimes.get(str(r), 0) + 1
    return {
        "trade_count": metrics.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "gross_pnl": metrics.get("gross_pnl"),
        "fees": metrics.get("total_fees"),
        "net_pnl": metrics.get("net_pnl"),
        "average_net_r": metrics.get("net_avg_r"),
        "profit_factor": metrics.get("profit_factor"),
        "maximum_drawdown": metrics.get("max_drawdown_r"),
        "maximum_losing_streak": metrics.get("max_losing_streak"),
        "avg_mfe": (sum(mfes) / len(mfes)) if mfes else None,
        "avg_mae": (sum(maes) / len(maes)) if maes else None,
        "regime_distribution": regimes,
        "closed_trades": enriched,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


def _filter_retest(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        et = str(
            t.get("entry_type")
            or (t.get("condition_snapshot") or {}).get("entry_type")
            or ""
        ).upper()
        if et == "LIMIT_RETEST" or t.get("entry_quality") == "RETEST":
            out.append(dict(t))
    return out


def _filter_strong_bear(diag_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in diag_rows
        if REGIME_STRONG_BEAR in (r.get("regimes") or [])
        or r.get("primary_regime") == REGIME_STRONG_BEAR
    ]


def _resim_wider_stop(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    atr_buffer_mult: float = 0.5,
) -> dict[str, Any]:
    entry_idx = trade.get("entry_index")
    if entry_idx is None:
        return dict(trade)
    atr = trade.get("atr_at_signal") or 0.0
    stop = float(trade["stop_price"]) + float(atr) * atr_buffer_mult
    rt = ResearchTrade(
        symbol=str(trade.get("symbol") or ""),
        timeframe=str(trade.get("timeframe") or "1h"),
        combination_id=str(trade.get("combination_id") or "COMBO_02"),
        entry_index=int(entry_idx),
        signal_time=trade.get("entry_time") or trade.get("signal_time"),
        direction="SHORT",
        entry_price=float(trade["entry_price"]),
        stop_price=stop,
        tp1=trade.get("TP1") or trade.get("tp1"),
        tp2=trade.get("TP2") or trade.get("tp2"),
        tp3=trade.get("TP3") or trade.get("tp3"),
        rr=trade.get("reward_to_risk_ratio") or trade.get("rr"),
        condition_snapshot=dict(trade.get("condition_snapshot") or {}),
    )
    sim = simulate_research_trade(rt, candles)
    row = sim.to_dict()
    row["variant_stop"] = stop
    row["atr_buffer_mult"] = atr_buffer_mult
    return row


def _resim_support_tp(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Set TP1 to nearby support (recent swing low) when available; else 1.5R."""
    entry = float(trade["entry_price"])
    stop = float(trade["stop_price"])
    risk = initial_risk(entry, stop)
    support = trade.get("support_level") or trade.get("recent_swing_low")
    if support is not None and float(support) < entry:
        tp1 = float(support)
    else:
        tp1 = entry - 1.5 * risk
    entry_idx = trade.get("entry_index")
    if entry_idx is None:
        row = dict(trade)
        row["tp1"] = tp1
        return row
    rt = ResearchTrade(
        symbol=str(trade.get("symbol") or ""),
        timeframe=str(trade.get("timeframe") or "1h"),
        combination_id=str(trade.get("combination_id") or "COMBO_02"),
        entry_index=int(entry_idx),
        signal_time=trade.get("entry_time") or trade.get("signal_time"),
        direction="SHORT",
        entry_price=entry,
        stop_price=stop,
        tp1=tp1,
        tp2=None,
        tp3=None,
        rr=1.5,
        condition_snapshot=dict(trade.get("condition_snapshot") or {}),
    )
    sim = simulate_research_trade(rt, candles)
    row = sim.to_dict()
    row["variant_tp1"] = tp1
    return row


def run_no_htf_backtest(
    *,
    symbol: str,
    candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    combo = get_combination("COMBO_02_LOCAL")
    assert combo is not None
    out = run_combination_backtest(
        symbol,
        "1h",
        candles,
        combo,
        signal_config=SignalConfig(),
        direction_filter="SHORT",
        candles_1h=candles,
        candles_4h=candles_4h,
    )
    return [t if isinstance(t, dict) else t.to_dict() for t in (out.get("trades") or [])]


def attach_direct_replay(
    trades: Sequence[Mapping[str, Any]],
    *,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    enriched = []
    for t in trades:
        row = enrich_trade_forensic_fields({**t, "direction": "SHORT"})
        try:
            row = apply_independent_replay_to_trade(
                row, candles_1h=candles_1h, candles_4h=candles_4h or []
            )
        except Exception:
            pass
        enriched.append(row)
    audit = audit_trades_for_lookahead(enriched, timeframe="1h")
    return {"trades": enriched, "forensic": audit}


def build_ablation_matrix(
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    diagnostic_rows: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    candles_4h_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    window: Mapping[str, Any],
    symbols: Sequence[str],
    risk_usd: float = 20.0,
    run_no_htf: bool = True,
) -> dict[str, Any]:
    """Build ablation results. Exploratory — not OOS-validated winners."""
    c4h = candles_4h_by_symbol or {}
    matrix: dict[str, Any] = {}

    # Baseline
    base_summary = summarize_variant_trades(baseline_trades, risk_usd=risk_usd)
    matrix[VARIANT_BASELINE] = {
        **VARIANT_SPECS[VARIANT_BASELINE],
        "variant_id": VARIANT_BASELINE,
        "fingerprint": variant_fingerprint(VARIANT_BASELINE, window=window, symbols=symbols),
        **{k: v for k, v in base_summary.items() if k != "closed_trades"},
        "direct_candle_replay": True,
        "oos_status": "NOT_RUN_BASE_FAILED_OR_DIAGNOSTIC",
        "exploratory_only": True,
        **SAFETY_STAMPS,
    }

    # Retest-only (filter)
    retest_trades = _filter_retest(diagnostic_rows) or _filter_retest(baseline_trades)
    retest_summary = summarize_variant_trades(retest_trades, risk_usd=risk_usd)
    matrix[VARIANT_RETEST_ONLY] = {
        **VARIANT_SPECS[VARIANT_RETEST_ONLY],
        "variant_id": VARIANT_RETEST_ONLY,
        "fingerprint": variant_fingerprint(VARIANT_RETEST_ONLY, window=window, symbols=symbols),
        **{k: v for k, v in retest_summary.items() if k != "closed_trades"},
        "method": "filter_baseline_retest_entries",
        "exploratory_only": True,
        **SAFETY_STAMPS,
    }

    # Strong-bear filter
    sb_trades = _filter_strong_bear(diagnostic_rows)
    sb_summary = summarize_variant_trades(sb_trades, risk_usd=risk_usd)
    matrix[VARIANT_STRONG_BEAR] = {
        **VARIANT_SPECS[VARIANT_STRONG_BEAR],
        "variant_id": VARIANT_STRONG_BEAR,
        "fingerprint": variant_fingerprint(VARIANT_STRONG_BEAR, window=window, symbols=symbols),
        **{k: v for k, v in sb_summary.items() if k != "closed_trades"},
        "method": "filter_regime_STRONG_BEAR",
        "exploratory_only": True,
        **SAFETY_STAMPS,
    }

    # Wider stop re-sim
    wider_rows: list[dict[str, Any]] = []
    for row in diagnostic_rows:
        sym = str(row.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        if not candles or row.get("entry_index") is None:
            continue
        wider_rows.append(_resim_wider_stop(row, candles))
    wider_summary = summarize_variant_trades(wider_rows, risk_usd=risk_usd)
    matrix[VARIANT_WIDER_STOP] = {
        **VARIANT_SPECS[VARIANT_WIDER_STOP],
        "variant_id": VARIANT_WIDER_STOP,
        "fingerprint": variant_fingerprint(VARIANT_WIDER_STOP, window=window, symbols=symbols),
        **{k: v for k, v in wider_summary.items() if k != "closed_trades"},
        "method": "resim_stop_plus_0_5ATR",
        "exploratory_only": True,
        **SAFETY_STAMPS,
    }

    # Support TP re-sim
    support_rows: list[dict[str, Any]] = []
    for row in diagnostic_rows:
        sym = str(row.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        if not candles or row.get("entry_index") is None:
            continue
        support_rows.append(_resim_support_tp(row, candles))
    support_summary = summarize_variant_trades(support_rows, risk_usd=risk_usd)
    matrix[VARIANT_SUPPORT_TP] = {
        **VARIANT_SPECS[VARIANT_SUPPORT_TP],
        "variant_id": VARIANT_SUPPORT_TP,
        "fingerprint": variant_fingerprint(VARIANT_SUPPORT_TP, window=window, symbols=symbols),
        **{k: v for k, v in support_summary.items() if k != "closed_trades"},
        "method": "resim_tp_at_support_or_1_5R",
        "exploratory_only": True,
        **SAFETY_STAMPS,
    }

    # No-HTF full rebacktest
    if run_no_htf:
        no_htf_trades: list[dict[str, Any]] = []
        for sym in symbols:
            candles = candles_by_symbol.get(str(sym).upper()) or []
            if not candles:
                continue
            no_htf_trades.extend(
                run_no_htf_backtest(
                    symbol=str(sym).upper(),
                    candles=candles,
                    candles_4h=c4h.get(str(sym).upper()),
                )
            )
        no_htf_closed = [
            t
            for t in no_htf_trades
            if str(t.get("outcome") or "") not in ("", "OPEN", "None")
        ]
        no_htf_summary = summarize_variant_trades(no_htf_closed, risk_usd=risk_usd)
        matrix[VARIANT_NO_HTF] = {
            **VARIANT_SPECS[VARIANT_NO_HTF],
            "variant_id": VARIANT_NO_HTF,
            "fingerprint": variant_fingerprint(VARIANT_NO_HTF, window=window, symbols=symbols),
            **{k: v for k, v in no_htf_summary.items() if k != "closed_trades"},
            "method": "COMBO_02_LOCAL_SHORT_rebacktest",
            "exploratory_only": True,
            **SAFETY_STAMPS,
        }

    # Fingerprints must all be unique
    fps = [matrix[k]["fingerprint"] for k in matrix]
    matrix["_meta"] = {
        "fingerprints_unique": len(fps) == len(set(fps)),
        "variant_count": len([k for k in matrix if not k.startswith("_")]),
        "cannot_create_paper_trade": True,
        "cannot_change_v1": True,
        "note": (
            "Do not choose a winner from net PnL alone. In-sample improvements "
            "are exploratory until disjoint OOS validation."
        ),
        **SAFETY_STAMPS,
    }
    return matrix


def reject_variant_reasons(summary: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    n = int(summary.get("trade_count") or 0)
    if n < 20:
        reasons.append("too_few_trades")
    if summary.get("maximum_drawdown") is not None and float(summary["maximum_drawdown"]) > 15:
        reasons.append("large_drawdown")
    if summary.get("oos_status") not in (None, "PASS"):
        # diagnostic phase — expected
        pass
    return reasons
