"""Curated strategy catalog for the Strategies UI.

Read-only operator view of what is working vs research-only.
Does not alter entry logic, paper paths, or Telegram eligibility.
"""

from __future__ import annotations

from typing import Any

from app.research.bos_combinations import COMBINATIONS, list_combinations
from app.research.v1_production import (
    COMBO_ID,
    COMBO_VERSION,
    EXTENDED_PAPER_SYMBOLS,
    FROZEN_V1_SYMBOLS,
    V1_SYMBOLS,
    profile_summary,
)


def build_strategy_catalog() -> dict[str, Any]:
    combos = {c["combination_id"]: c for c in list_combinations()}
    profile = profile_summary()
    enabled_1h = [
        b
        for b in profile["books"]
        if b.get("timeframe") == "1h" and b.get("enabled_by_default")
    ]

    strategies: list[dict[str, Any]] = [
        {
            "id": "COMBO_02_V1",
            "name": "COMBO_02 v1 — HTF-gated LONG",
            "status": "WORKING",
            "tier": "production_candidate",
            "combo_id": COMBO_ID,
            "combo_version": COMBO_VERSION,
            "direction": "LONG",
            "setup_timeframe": "1h",
            "htf_timeframes": ["4h", "1h"],
            "summary": (
                "Primary working long playbook. Requires setup-TF bullish structure "
                "(HH+HL), confirmed bullish BOS, and hard 4h+1h HTF alignment."
            ),
            "gates": list(COMBINATIONS["COMBO_02"].conditions),
            "symbols": {
                "freeze_claim": sorted(FROZEN_V1_SYMBOLS),
                "paper_watch": sorted(V1_SYMBOLS),
                "extended_paper": sorted(EXTENDED_PAPER_SYMBOLS),
            },
            "risk": {
                "default_percent": 0.02,
                "display": "2% of equity per trade (1R)",
                "example_equity_usd": 1000,
                "example_risk_usd": 20,
            },
            "where_to_run": [
                {"label": "Paper blotter", "path": "/paper"},
                {"label": "Strategy backtest", "path": "/backtest"},
                {"label": "Screener v1 watcher", "path": "/"},
            ],
            "do_not": list(profile.get("do_not") or []),
            "example": _combo02_example(),
            "combo_definition": combos.get("COMBO_02"),
        },
        {
            "id": "COMBO_02_SHORT_RESEARCH",
            "name": "COMBO_02 SHORT — research only",
            "status": "RESEARCH",
            "tier": "research_only",
            "combo_id": "COMBO_02",
            "direction": "SHORT",
            "setup_timeframe": "1h",
            "htf_timeframes": ["4h", "1h"],
            "summary": (
                "Bearish mirror of COMBO_02 for research. Never opens paper/live "
                "or Telegram from the SHORT research panel."
            ),
            "gates": [
                "Setup-TF bearish structure (LH+LL)",
                "Confirmed bearish BOS",
                "4h+1h HTF bearish alignment (fail closed)",
            ],
            "where_to_run": [
                {"label": "BOS Research", "path": "/bos-research"},
            ],
            "example": {
                "title": "Example SHORT setup (illustrative)",
                "steps": [
                    "4h trend BEARISH and 1h trend BEARISH",
                    "1h prints LH+LL and confirms bearish BOS",
                    "Enter on retest of broken level (or close rule)",
                    "Stop above structural swing; TP1 ≥ 2R",
                ],
                "note": "Research-only — not Path A production.",
            },
            "combo_definition": combos.get("COMBO_02"),
        },
        {
            "id": "COMBO_03",
            "name": "COMBO_03 — Trend + BOS + Pullback",
            "status": "RESEARCH",
            "tier": "research_only",
            "combo_id": "COMBO_03",
            "direction": "LONG / SHORT",
            "setup_timeframe": "15m or 1h",
            "summary": COMBINATIONS["COMBO_03"].description,
            "gates": list(COMBINATIONS["COMBO_03"].conditions),
            "where_to_run": [
                {"label": "BOS Research", "path": "/bos-research"},
            ],
            "example": {
                "title": "Example LONG pullback (illustrative)",
                "steps": [
                    "Trend BULLISH (HH+HL) on setup TF",
                    "Bullish BOS confirmed",
                    "Pullback ACTIVE/CONFIRMED without breaking HL",
                    "Enter after pullback holds; stop below pullback low; TP1 ≥ 2R",
                ],
            },
            "combo_definition": combos.get("COMBO_03"),
        },
        {
            "id": "COMBO_04",
            "name": "COMBO_04 — Trend + BOS + Impulse + Pullback",
            "status": "RESEARCH",
            "tier": "research_only",
            "combo_id": "COMBO_04",
            "direction": "LONG / SHORT",
            "setup_timeframe": "15m or 1h",
            "summary": COMBINATIONS["COMBO_04"].description,
            "gates": list(COMBINATIONS["COMBO_04"].conditions),
            "where_to_run": [
                {"label": "BOS Research", "path": "/bos-research"},
            ],
            "example": {
                "title": "Example impulse→pullback LONG",
                "steps": [
                    "Bullish BOS + trend agree",
                    "Impulse displacement confirms after BOS",
                    "Pullback into impulse origin / demand holds",
                    "Enter on hold/retest; structural stop; TP1 ≥ 2R",
                ],
            },
            "combo_definition": combos.get("COMBO_04"),
        },
        {
            "id": "COMBO_02_LOCAL",
            "name": "COMBO_02_LOCAL — setup-TF only (A/B)",
            "status": "BASELINE",
            "tier": "research_baseline",
            "combo_id": "COMBO_02_LOCAL",
            "direction": "LONG",
            "setup_timeframe": "1h / 15m",
            "summary": COMBINATIONS["COMBO_02_LOCAL"].description,
            "gates": list(COMBINATIONS["COMBO_02_LOCAL"].conditions),
            "where_to_run": [
                {"label": "BOS Research", "path": "/bos-research"},
            ],
            "example": {
                "title": "Why it exists",
                "steps": [
                    "Same Trend+BOS as COMBO_02 but without 4h+1h hard gate",
                    "Use only to measure how much HTF filtering changes results",
                    "Never label as COMBO_02 v1 / never Telegram as v1",
                ],
            },
            "combo_definition": combos.get("COMBO_02_LOCAL"),
        },
        {
            "id": "PATH_B_EXPERIMENTAL",
            "name": "Path B — full setup (experimental)",
            "status": "EXPERIMENTAL",
            "tier": "experimental",
            "direction": "LONG",
            "setup_timeframe": "15m (+ MTF)",
            "summary": (
                "Stricter live Trade Plan path: Path A gates plus impulse, pullback, "
                "retest, and MTF. Not COMBO_02 v1."
            ),
            "gates": [
                "Path A / structure + BOS",
                "Impulse PASS",
                "Pullback ACTIVE or CONFIRMED",
                "Retest PASS or pullback CONFIRMED",
                "MTF not CONFLICT",
                "TP1 ≥ min RR",
            ],
            "where_to_run": [
                {"label": "Paper (Path B label)", "path": "/paper"},
                {"label": "Screener Trade Plan", "path": "/"},
            ],
            "example": {
                "title": "Example Path B LONG",
                "steps": [
                    "15m HH+HL + bullish BOS",
                    "4h and 1h not bearish / not CONFLICT",
                    "Impulse then pullback holds structure",
                    "Status becomes LONG_ENTRY_CANDIDATE (never auto BUY)",
                ],
                "note": "Often WAITING on pullback — do not fake PASS.",
            },
        },
    ]

    return {
        "label": "Working strategies catalog",
        "disclaimer": (
            "Historical / paper research only. Not a claim of future profitability. "
            "UI ENTRY candidates are never automatic BUY orders."
        ),
        "freeze_tags": {
            "strategy_logic": "v1-combo02-long-htf",
            "pre_research_baseline": "pre-research-freeze-20261005",
        },
        "primary_working": "COMBO_02_V1",
        "v1_profile": {
            "combo_id": profile["combo_id"],
            "combo_version": profile["combo_version"],
            "enabled_1h_books": enabled_1h,
            "freeze_claim_symbols": sorted(FROZEN_V1_SYMBOLS),
            "paper_watch_count": len(V1_SYMBOLS),
        },
        "strategies": strategies,
        "all_combinations": list_combinations(),
        "sizing_example": {
            "title": "Position sizing example (COMBO_02 v1 style)",
            "equity_usd": 1000,
            "risk_percent": 0.02,
            "risk_usd": 20,
            "entry": 2700,
            "stop": 2660,
            "risk_per_unit": 40,
            "qty": 0.5,
            "tp1_2r": 2780,
            "outcome": "TP1 ≈ +$40 (+2R); stop ≈ −$20 (−1R)",
        },
    }


def _combo02_example() -> dict[str, Any]:
    return {
        "title": "Worked example — BTCUSDT 1h COMBO_02 LONG",
        "symbol": "BTCUSDT",
        "setup_tf": "1h",
        "scenario": [
            {
                "step": 1,
                "check": "4h trend",
                "result": "BULLISH (HH+HL)",
                "pass": True,
            },
            {
                "step": 2,
                "check": "1h HTF trend",
                "result": "BULLISH (HH+HL)",
                "pass": True,
            },
            {
                "step": 3,
                "check": "Setup 1h BOS",
                "result": "CONFIRMED BULLISH_BOS after HL hold",
                "pass": True,
            },
            {
                "step": 4,
                "check": "HTF gate",
                "result": "HTF_ALIGNED (4h+1h both bullish)",
                "pass": True,
            },
            {
                "step": 5,
                "check": "Risk",
                "result": "Entry 67,200 · Stop 66,400 · risk $800/unit",
                "pass": True,
            },
            {
                "step": 6,
                "check": "Size @ $1,000 / 2%",
                "result": "1R=$20 → qty = 20/800 = 0.025",
                "pass": True,
            },
            {
                "step": 7,
                "check": "TP1 (2R)",
                "result": "67,200 + 2×800 = 68,800",
                "pass": True,
            },
        ],
        "fail_closed_examples": [
            "4h BEARISH → no long (HTF_CONFLICT / fail closed)",
            "1h HTF missing → no long (HTF_NEUTRAL_UNAVAILABLE)",
            "BOS without HH+HL trend → no long",
            "COMBO_02_LOCAL would still fire without HTF — that is why it is not v1",
        ],
        "note": (
            "Illustrative numbers for teaching the checklist. Live fills use paper "
            "sizing (tick/lot/leverage/fees) on the Path A watcher."
        ),
    }
