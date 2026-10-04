"""Strategy Backtest UI/API configuration safety (no trading-logic changes).

Resolves effective v1 risk, SHORT pause rejection, strategy identity, timeframe
roles, lookback duration labels, and production-comparable metadata. Does not
alter signal generation, entry/SL/TP/PnL/fee math, paper/live execution, or Telegram.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from app.research.v1_production import (
    COMBO_ID,
    CORE_SYMBOLS,
    DEFAULT_PRINCIPAL_USD,
    SECONDARY_SYMBOLS,
    V1_PAPER_RISK_BY_SYMBOL,
    V1_SYMBOLS,
    classify_tier,
    normalize_symbol,
    normalize_timeframe,
    recommended_risk_usd,
    risk_percent_for,
)

RiskSource = Literal["V1_PRODUCTION_PROFILE", "RESEARCH_OVERRIDE", "RESEARCH_FALLBACK"]
PeriodMode = Literal["DB_TAIL", "CALENDAR_RANGE"]

STRATEGY_ID_V1 = "COMBO_02_V1"
COMBO_VERSION_V1 = "v1"
SOURCE_V1_RESEARCH_BACKTEST = "V1_RESEARCH_BACKTEST"
SHORT_RESEARCH_PAUSED_CODE = "short_research_paused"

# Frozen COMBO_02 v1 production setup (docs/v1_freeze.md / v1_production.py).
V1_SETUP_TIMEFRAME = "1h"
V1_HTF_TIMEFRAMES = ("1h", "4h")
V1_HTF_ALIGNMENT = "BULLISH"
V1_DIRECTION = "LONG"
V1_DEFAULT_LEVERAGE = 2.0
V1_DEFAULT_TAKER_FEE_PCT = 0.04
V1_DEFAULT_MAKER_FEE_PCT = 0.02

TF_MINUTES: dict[str, int] = {"15m": 15, "1h": 60, "4h": 240, "5m": 5, "1d": 1440}

TIMEFRAME_ROLES: dict[str, str] = {
    "15m": "research-only",
    "1h": "v1 setup timeframe",
    "4h": "HTF context",
}


class BacktestConfigError(ValueError):
    """Validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


def bars_to_approx_days(bars: int, timeframe: str) -> float | None:
    """Approximate calendar days covered by N bars at the given timeframe."""
    mins = TF_MINUTES.get(normalize_timeframe(timeframe))
    if mins is None or bars <= 0:
        return None
    return round(float(bars) * mins / (60.0 * 24.0), 1)


def timeframe_role_label(timeframe: str) -> str:
    tf = normalize_timeframe(timeframe)
    return TIMEFRAME_ROLES.get(tf, "research-only")


def symbol_role(symbol: str, timeframe: str | None = None) -> str | None:
    """Return CORE / SECONDARY / RESEARCH for v1 books; None outside universe."""
    sym = normalize_symbol(symbol)
    tf = normalize_timeframe(timeframe) if timeframe else V1_SETUP_TIMEFRAME
    tier = classify_tier(sym, tf)
    if tier == "core":
        return "CORE"
    if tier == "secondary":
        return "SECONDARY"
    if tier == "research":
        return "RESEARCH"
    if sym in CORE_SYMBOLS:
        return "CORE"
    if sym in SECONDARY_SYMBOLS:
        return "SECONDARY"
    if sym in V1_SYMBOLS:
        return "RESEARCH"
    return None


def symbol_role_display(symbol: str, timeframe: str | None = None) -> str:
    role = symbol_role(symbol, timeframe)
    if role == "CORE":
        return f"{normalize_symbol(symbol)} — v1 CORE"
    if role == "SECONDARY":
        return f"{normalize_symbol(symbol)} — v1 SECONDARY"
    if role == "RESEARCH":
        return f"{normalize_symbol(symbol)} — research-only"
    return f"{normalize_symbol(symbol)} — non-v1"


def production_risk_table(principal_usd: float = DEFAULT_PRINCIPAL_USD) -> list[dict[str, Any]]:
    """Frozen v1 production risk display rows."""
    principal = max(1.0, float(principal_usd))
    rows: list[dict[str, Any]] = []
    for sym, pct in (
        ("BTCUSDT", 0.015),
        ("ETHUSDT", 0.005),
        ("SOLUSDT", 0.005),
    ):
        role = "core" if sym == "BTCUSDT" else "secondary"
        rows.append(
            {
                "symbol": sym,
                "role": role.upper(),
                "role_label": role,
                "risk_percent": pct,
                "risk_pct_display": f"{pct * 100:g}%",
                "risk_amount": round(principal * pct, 2),
                "display": (
                    f"{sym} {role} — {pct * 100:g}% / ${principal * pct:g}"
                ),
            }
        )
    return rows


def fee_display_metadata(
    *,
    taker_fee_pct: float = V1_DEFAULT_TAKER_FEE_PCT,
    maker_fee_pct: float = V1_DEFAULT_MAKER_FEE_PCT,
) -> dict[str, Any]:
    """Display-only fee/entry metadata — does not change fee calculations."""
    return {
        "entry_type": "MARKET or LIMIT_RETEST",
        "entry_fee_type": "Market entry -> taker; Limit retest entry -> maker",
        "exit_type": "MARKET",
        "exit_fee_type": "Market exit -> taker",
        "fee_basis": "executed notional",
        "fee_convention": "negative cost",
        "taker_fee_pct": float(taker_fee_pct),
        "maker_fee_pct": float(maker_fee_pct),
        "examples": [
            "Market entry -> taker fee",
            "Limit retest entry -> maker fee",
            "Market exit -> taker fee",
            "Fee basis -> executed notional",
        ],
    }


def period_mode_label(
    *,
    start_date: str | None,
    end_date: str | None,
) -> PeriodMode:
    if start_date or end_date:
        return "CALENDAR_RANGE"
    return "DB_TAIL"


def period_mode_copy(mode: PeriodMode) -> dict[str, str]:
    if mode == "CALENDAR_RANGE":
        return {
            "mode": "CALENDAR_RANGE",
            "label": "Calendar-range mode",
            "detail": "Uses only candles inside the requested UTC range",
        }
    return {
        "mode": "DB_TAIL",
        "label": "DB-tail mode",
        "detail": "Uses the latest N available candles",
        "note": "Does not represent a calendar-year filter",
    }


def configuration_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ResolvedCellRisk:
    symbol: str
    timeframe: str
    configured_risk_percent: float
    effective_risk_percent: float
    effective_risk_amount: float
    risk_source: RiskSource
    production_comparable: bool
    symbol_role: str | None


@dataclass(frozen=True)
class ResolvedBacktestConfig:
    strategy_id: str
    combo_version: str
    source: str
    direction: str
    setup_timeframe: str
    htf_timeframes: list[str]
    htf_alignment: str
    risk_mode: str
    configured_risk_percent: float
    principal_usd: float
    leverage: float
    production_comparable: bool
    research_only: bool
    mismatch_reasons: list[str]
    period_mode: PeriodMode
    paper_trade_created: bool
    live_trade_created: bool
    telegram_sent: bool
    short_status: str
    fee_metadata: dict[str, Any]
    cells: list[ResolvedCellRisk]
    configuration_fingerprint: str


def _configured_risk_percent(
    *,
    risk_usd: float | None,
    risk_percent: float | None,
    principal_usd: float,
) -> float:
    principal = max(1.0, float(principal_usd))
    if risk_percent is not None:
        pct = float(risk_percent)
        if pct > 1.0:
            pct = pct / 100.0
        return pct
    if risk_usd is not None:
        return float(risk_usd) / principal
    return 0.02


def validate_backtest_request(
    *,
    symbols: list[str],
    timeframes: list[str],
    direction: str | None,
    combination_id: str = COMBO_ID,
    strategy_id: str | None = None,
    combo_version: str | None = None,
    setup_timeframe: str | None = None,
    risk_mode: str | None = None,
    research_risk_override: bool = False,
    risk_usd: float | None = None,
    risk_percent: float | None = None,
    principal_usd: float = DEFAULT_PRINCIPAL_USD,
    leverage: float = V1_DEFAULT_LEVERAGE,
    taker_fee_pct: float = V1_DEFAULT_TAKER_FEE_PCT,
    maker_fee_pct: float = V1_DEFAULT_MAKER_FEE_PCT,
    start_date: str | None = None,
    end_date: str | None = None,
    allow_short: bool = False,
) -> ResolvedBacktestConfig:
    """Validate UI/API request and resolve effective risk / comparability metadata."""
    syms = [normalize_symbol(s) for s in symbols if s and str(s).strip()]
    tfs = [normalize_timeframe(t) for t in timeframes if t and str(t).strip()]
    if not syms:
        raise BacktestConfigError("symbols_required", "At least one symbol is required")
    if not tfs:
        raise BacktestConfigError(
            "timeframes_required", "At least one supported timeframe is required"
        )

    direction_raw = (direction or "").strip().upper()
    if not direction_raw or direction_raw in {"ALL", "BOTH", "ANY", "AMBIGUOUS"}:
        raise BacktestConfigError(
            "ambiguous_direction",
            "Direction must be explicitly LONG (SHORT research is paused)",
        )
    if direction_raw not in {"LONG", "SHORT"}:
        raise BacktestConfigError(
            "ambiguous_direction",
            f"Unsupported direction {direction_raw!r}",
        )

    if direction_raw == "SHORT" and not allow_short:
        raise BacktestConfigError(
            SHORT_RESEARCH_PAUSED_CODE,
            "SHORT research is paused. SHORT paper trading, production, and Telegram "
            "are disabled. No SHORT backtest will enter an operational path.",
        )

    if risk_usd is not None and float(risk_usd) <= 0:
        raise BacktestConfigError("invalid_risk", "Risk must be positive")
    if risk_percent is not None:
        pct_in = float(risk_percent)
        pct_frac = pct_in / 100.0 if pct_in > 1.0 else pct_in
        if pct_frac <= 0:
            raise BacktestConfigError("invalid_risk", "Risk must be positive")

    combo = (combination_id or COMBO_ID).upper().strip()
    sid = (strategy_id or STRATEGY_ID_V1).upper().strip()
    cver = (combo_version or COMBO_VERSION_V1).strip().lower()
    setup_tf = normalize_timeframe(setup_timeframe or (tfs[0] if len(tfs) == 1 else V1_SETUP_TIMEFRAME))
    if setup_timeframe is None and len(tfs) == 1:
        setup_tf = tfs[0]

    configured_pct = _configured_risk_percent(
        risk_usd=risk_usd,
        risk_percent=risk_percent,
        principal_usd=principal_usd,
    )
    principal = max(100.0, min(float(principal_usd), 10_000_000.0))
    lev = max(1.0, min(float(leverage or V1_DEFAULT_LEVERAGE), 125.0))

    mode = (risk_mode or "").upper().strip()
    if not mode:
        mode = (
            "RESEARCH_OVERRIDE"
            if research_risk_override
            else "V1_PRODUCTION_PROFILE"
        )
    if research_risk_override:
        mode = "RESEARCH_OVERRIDE"

    cells: list[ResolvedCellRisk] = []
    mismatch: list[str] = []

    if direction_raw != V1_DIRECTION:
        mismatch.append("direction_mismatch")
    if combo != COMBO_ID or sid not in {STRATEGY_ID_V1, COMBO_ID, "COMBO_02_V1"}:
        mismatch.append("non_v1_strategy")
    if cver not in {COMBO_VERSION_V1, "v1-combo02-long-htf"}:
        mismatch.append("non_v1_strategy")

    for tf in tfs:
        if tf not in TF_MINUTES and tf not in {"15m", "1h", "4h", "5m", "1d"}:
            raise BacktestConfigError(
                "unsupported_timeframe",
                f"Unsupported setup/timeframe combination: {tf}",
            )
        if tf == "4h":
            mismatch.append("setup_timeframe_mismatch")
        elif tf == "15m":
            mismatch.append("setup_timeframe_mismatch")
        elif tf != V1_SETUP_TIMEFRAME:
            mismatch.append("setup_timeframe_mismatch")

    for sym in syms:
        if sym not in V1_SYMBOLS:
            mismatch.append("non_v1_symbol")

    # HTF is fixed for COMBO_02 v1 — any non-LONG or non-COMBO_02 already mismatched.
    # Selecting only non-1h setups is already setup_timeframe_mismatch.
    if abs(float(lev) - V1_DEFAULT_LEVERAGE) > 1e-9:
        mismatch.append("custom_leverage")
    if abs(float(taker_fee_pct) - V1_DEFAULT_TAKER_FEE_PCT) > 1e-9:
        mismatch.append("custom_fee_model")
    if abs(float(maker_fee_pct) - V1_DEFAULT_MAKER_FEE_PCT) > 1e-9:
        mismatch.append("custom_fee_model")

    for sym in syms:
        for tf in tfs:
            tier = classify_tier(sym, tf)
            v1_pct = risk_percent_for(sym, tf)
            role = symbol_role(sym, tf)
            use_v1 = (
                mode == "V1_PRODUCTION_PROFILE"
                and combo == COMBO_ID
                and direction_raw == V1_DIRECTION
                and tier in ("core", "secondary")
                and v1_pct is not None
                and v1_pct > 0
            )
            if use_v1:
                eff_pct = float(v1_pct)
                eff_amt = recommended_risk_usd(
                    sym, tf, principal_usd=principal, fallback_risk_usd=principal * configured_pct
                )
                src: RiskSource = "V1_PRODUCTION_PROFILE"
                cell_ok = (
                    tf == V1_SETUP_TIMEFRAME
                    and sym in V1_SYMBOLS
                    and abs(eff_pct - float(V1_PAPER_RISK_BY_SYMBOL.get(sym, -1))) < 1e-12
                )
            elif mode == "RESEARCH_OVERRIDE":
                eff_pct = configured_pct
                eff_amt = max(1.0, principal * configured_pct)
                src = "RESEARCH_OVERRIDE"
                cell_ok = False
                mismatch.append("risk_mismatch")
            else:
                # Research books / unknown: keep configured risk, not production-comparable.
                eff_pct = configured_pct
                eff_amt = max(1.0, principal * configured_pct)
                src = "RESEARCH_FALLBACK"
                cell_ok = False
                if tier in ("core", "secondary") and v1_pct and abs(configured_pct - v1_pct) > 1e-12:
                    mismatch.append("risk_mismatch")
                if tier not in ("core", "secondary"):
                    # research TF or non-book
                    pass

            # Silent 2% must never look production-comparable for v1 symbols.
            if (
                sym in V1_SYMBOLS
                and tf == V1_SETUP_TIMEFRAME
                and src != "V1_PRODUCTION_PROFILE"
            ):
                cell_ok = False
                if "risk_mismatch" not in mismatch:
                    mismatch.append("risk_mismatch")

            cells.append(
                ResolvedCellRisk(
                    symbol=sym,
                    timeframe=tf,
                    configured_risk_percent=configured_pct,
                    effective_risk_percent=eff_pct,
                    effective_risk_amount=float(eff_amt),
                    risk_source=src,
                    production_comparable=bool(cell_ok),
                    symbol_role=role,
                )
            )

    # Deduplicate mismatch reasons preserving order.
    seen: set[str] = set()
    uniq_mismatch: list[str] = []
    for m in mismatch:
        if m not in seen:
            seen.add(m)
            uniq_mismatch.append(m)

    all_cells_ok = bool(cells) and all(c.production_comparable for c in cells)
    # Exact frozen v1 also requires default fees/leverage and COMBO_02 LONG 1h only.
    production_comparable = (
        all_cells_ok
        and not uniq_mismatch
        and direction_raw == V1_DIRECTION
        and combo == COMBO_ID
        and mode == "V1_PRODUCTION_PROFILE"
    )
    # If only setup_timeframe_mismatch from 4h/15m but cells marked ok incorrectly — fix.
    if any(c.timeframe != V1_SETUP_TIMEFRAME for c in cells):
        production_comparable = False
        if "setup_timeframe_mismatch" not in uniq_mismatch:
            uniq_mismatch.append("setup_timeframe_mismatch")

    # Recompute: production_comparable only when every selected cell is frozen v1.
    production_comparable = bool(cells) and all(
        c.production_comparable
        and c.risk_source == "V1_PRODUCTION_PROFILE"
        and c.timeframe == V1_SETUP_TIMEFRAME
        for c in cells
    )
    if abs(float(lev) - V1_DEFAULT_LEVERAGE) > 1e-9:
        production_comparable = False
    if abs(float(taker_fee_pct) - V1_DEFAULT_TAKER_FEE_PCT) > 1e-9:
        production_comparable = False
    if abs(float(maker_fee_pct) - V1_DEFAULT_MAKER_FEE_PCT) > 1e-9:
        production_comparable = False
    if direction_raw != V1_DIRECTION or combo != COMBO_ID:
        production_comparable = False
    if mode != "V1_PRODUCTION_PROFILE":
        production_comparable = False

    if not production_comparable and "risk_mismatch" not in uniq_mismatch:
        # Ensure research-only configs always carry at least one reason when mismatched.
        if any(c.risk_source != "V1_PRODUCTION_PROFILE" for c in cells):
            if any(c.symbol in V1_SYMBOLS for c in cells):
                uniq_mismatch.append("risk_mismatch")

    pmode = period_mode_label(start_date=start_date, end_date=end_date)
    fp = configuration_fingerprint(
        {
            "strategy_id": STRATEGY_ID_V1 if combo == COMBO_ID else sid,
            "combo_version": COMBO_VERSION_V1,
            "direction": direction_raw,
            "symbols": syms,
            "timeframes": tfs,
            "setup_timeframe": setup_tf,
            "risk_mode": mode,
            "configured_risk_percent": configured_pct,
            "principal_usd": principal,
            "leverage": lev,
            "taker_fee_pct": float(taker_fee_pct),
            "maker_fee_pct": float(maker_fee_pct),
            "start_date": start_date,
            "end_date": end_date,
            "cells": [
                {
                    "symbol": c.symbol,
                    "timeframe": c.timeframe,
                    "effective_risk_percent": c.effective_risk_percent,
                    "risk_source": c.risk_source,
                }
                for c in cells
            ],
        }
    )

    return ResolvedBacktestConfig(
        strategy_id=STRATEGY_ID_V1 if combo == COMBO_ID else sid,
        combo_version=COMBO_VERSION_V1,
        source=SOURCE_V1_RESEARCH_BACKTEST,
        direction=direction_raw,
        setup_timeframe=setup_tf,
        htf_timeframes=list(V1_HTF_TIMEFRAMES),
        htf_alignment=V1_HTF_ALIGNMENT,
        risk_mode=mode,
        configured_risk_percent=configured_pct,
        principal_usd=principal,
        leverage=lev,
        production_comparable=production_comparable,
        research_only=not production_comparable,
        mismatch_reasons=uniq_mismatch,
        period_mode=pmode,
        paper_trade_created=False,
        live_trade_created=False,
        telegram_sent=False,
        short_status="PAUSED",
        fee_metadata=fee_display_metadata(
            taker_fee_pct=taker_fee_pct, maker_fee_pct=maker_fee_pct
        ),
        cells=cells,
        configuration_fingerprint=fp,
    )


def cell_risk_lookup(
    resolved: ResolvedBacktestConfig, symbol: str, timeframe: str
) -> ResolvedCellRisk | None:
    sym = normalize_symbol(symbol)
    tf = normalize_timeframe(timeframe)
    for c in resolved.cells:
        if c.symbol == sym and c.timeframe == tf:
            return c
    return None


def enrich_row_with_config(
    row: dict[str, Any],
    resolved: ResolvedBacktestConfig,
    *,
    dataset_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Attach identity/risk metadata to a matrix row without changing metrics."""
    cell = cell_risk_lookup(resolved, str(row.get("symbol") or ""), str(row.get("timeframe") or ""))
    out = dict(row)
    out["strategy_id"] = resolved.strategy_id
    out["combo_version"] = resolved.combo_version
    out["source"] = resolved.source
    out["setup_timeframe"] = str(row.get("timeframe") or resolved.setup_timeframe)
    out["htf_timeframes"] = list(resolved.htf_timeframes)
    out["htf_alignment"] = resolved.htf_alignment
    out["timeframe_role"] = timeframe_role_label(str(row.get("timeframe") or ""))
    out["symbol_role"] = cell.symbol_role if cell else symbol_role(
        str(row.get("symbol") or ""), str(row.get("timeframe") or "")
    )
    out["configured_risk_percent"] = (
        cell.configured_risk_percent if cell else resolved.configured_risk_percent
    )
    out["effective_risk_percent"] = (
        cell.effective_risk_percent if cell else resolved.configured_risk_percent
    )
    out["effective_risk_amount"] = (
        cell.effective_risk_amount if cell else float(row.get("risk_usd") or 0)
    )
    out["risk_source"] = cell.risk_source if cell else "RESEARCH_FALLBACK"
    out["production_comparable"] = bool(cell.production_comparable) if cell else False
    out["research_only"] = not out["production_comparable"]
    out["paper_trade_created"] = False
    out["live_trade_created"] = False
    out["telegram_sent"] = False
    out["requested_range"] = {
        "mode": resolved.period_mode,
        "start_date": None,  # filled by caller when known
        "end_date": None,
        "limit": None,
    }
    out["actual_range"] = {
        "first_candle": row.get("period_start"),
        "last_candle": row.get("period_end"),
        "bars_loaded": row.get("bars_loaded"),
        "bars_used": row.get("bars_loaded"),
    }
    out["approx_duration_days"] = bars_to_approx_days(
        int(row.get("bars_loaded") or 0), str(row.get("timeframe") or "")
    )
    out["dataset_fingerprint"] = dataset_fingerprint
    out["configuration_fingerprint"] = resolved.configuration_fingerprint
    out["fee_metadata"] = resolved.fee_metadata
    return out


def job_identity_payload(resolved: ResolvedBacktestConfig) -> dict[str, Any]:
    """Top-level identity/safety fields for job + start responses."""
    # Prefer first production cell risk for top-level convenience fields.
    primary = next((c for c in resolved.cells if c.production_comparable), None)
    if primary is None and resolved.cells:
        primary = resolved.cells[0]
    return {
        "strategy_id": resolved.strategy_id,
        "combo_version": resolved.combo_version,
        "source": resolved.source,
        "direction": resolved.direction,
        "setup_timeframe": resolved.setup_timeframe,
        "htf_timeframes": list(resolved.htf_timeframes),
        "htf_alignment": resolved.htf_alignment,
        "risk_mode": resolved.risk_mode,
        "configured_risk_percent": resolved.configured_risk_percent,
        "effective_risk_percent": (
            primary.effective_risk_percent if primary else resolved.configured_risk_percent
        ),
        "effective_risk_amount": (
            primary.effective_risk_amount
            if primary
            else resolved.principal_usd * resolved.configured_risk_percent
        ),
        "risk_source": primary.risk_source if primary else resolved.risk_mode,
        "symbol_role": primary.symbol_role if primary else None,
        "production_comparable": resolved.production_comparable,
        "research_only": resolved.research_only,
        "mismatch_reasons": list(resolved.mismatch_reasons),
        "short_status": resolved.short_status,
        "period_mode": resolved.period_mode,
        "period_mode_meta": period_mode_copy(resolved.period_mode),
        "production_risk_table": production_risk_table(resolved.principal_usd),
        "fee_metadata": resolved.fee_metadata,
        "paper_trade_created": False,
        "live_trade_created": False,
        "telegram_sent": False,
        "configuration_fingerprint": resolved.configuration_fingerprint,
        "safety_notice": (
            "Historical research only. Not a profitability claim. "
            "No paper or live trade created."
            if resolved.research_only
            else "Frozen v1 configuration — production-comparable research run. "
            "Historical research only. No paper or live trade created."
        ),
    }
