"""Research-only historical failure-regime decomposition for COMBO_02.

Generates trades with the existing COMBO_02 Path A backtest, then attaches
pre-entry research diagnostics. Does NOT modify live engines, thresholds,
BOS/trend/entry/SL/TP, or expose classifications as production signals.

Descriptive buckets are predeclared — not optimized.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.htf_alignment_sensitivity import (
    SAMPLE_INSUFFICIENT,
    SAMPLE_OK,
    SAMPLE_THRESHOLD,
    compute_metrics,
    sample_size_label,
)
from app.research.trend_regime_diagnostic import (
    TradeDiagRow,
    diagnose_at_entry,
    range_like_candidate,
)
from app.signals.config import SignalConfig
from app.signals.swing_detector import swings_for_timeframe

DATASET_LABEL = "BOS Combination Research"
COMBO_ID = "COMBO_02"

# Predeclared descriptive buckets (NOT optimized thresholds).
DIRECTION_CHANGE_BUCKETS = (
    ("0-2", lambda x: x is not None and 0 <= x <= 2),
    ("3-5", lambda x: x is not None and 3 <= x <= 5),
    ("6+", lambda x: x is not None and x >= 6),
)
RECENT_RANGE_ATR_BUCKETS = (
    ("<3", lambda x: x is not None and x < 3),
    ("3-5", lambda x: x is not None and 3 <= x <= 5),
    (">5", lambda x: x is not None and x > 5),
)
TREND_STRENGTH_BUCKETS = (
    ("<0.4", lambda x: x is not None and x < 0.4),
    ("0.4-0.7", lambda x: x is not None and 0.4 <= x <= 0.7),
    (">0.7", lambda x: x is not None and x > 0.7),
)
BOS_ATR_DISTANCE_BUCKETS = (
    ("<0.5", lambda x: x is not None and x < 0.5),
    ("0.5-1", lambda x: x is not None and 0.5 <= x <= 1.0),
    (">1", lambda x: x is not None and x > 1.0),
)

# Research-only POSSIBLE_CHOP / CLEAR_TREND proxies (descriptive, not production).
# POSSIBLE_CHOP reuses the existing RANGE_LIKE_CANDIDATE research rule OR
# high label-family flips with compact range (predeclared broad proxy).
CHOP_DIR_CHANGES_MIN = 4
CHOP_RANGE_ATR_MAX = 5.0
CHOP_STRENGTH_MAX = 0.55
CLEAR_DIR_CHANGES_MAX = 2
CLEAR_STRENGTH_MIN = 0.4
WEAK_BOS_ATR_MAX = 0.5

FEATURE_KEYS = (
    "trend_strength",
    "trend_duration_bars",
    "direction_changes",
    "recent_range_ATR",
    "ATR_percent",
    "BOS_ATR_distance",
    "bars_since_BOS",
    "HH_HL_sequence_length",
    "LH_LL_sequence_length",
    "swing_count",
    "structure_change_frequency",
    "range_width_ATR",
)

HTF_STATES = ("ALIGNED", "CONFLICT", "LOCAL_OR_NEUTRAL_HTF", "SETUP_NOT_TRENDING", "OTHER")


@dataclass
class FailureTradeRow:
    """Closed trade + research diagnostics (pre-entry features only)."""

    symbol: str
    timeframe: str
    direction: str
    entry_time: str | None
    exit_time: str | None
    result: str | None
    R: float | None
    setup_trend: str
    trend_strength: float | None
    trend_duration_bars: int | None
    HH_HL_sequence_length: int | None
    LH_LL_sequence_length: int | None
    bos_direction: str | None
    BOS_ATR_distance: float | None
    bars_since_BOS: int | None
    ATR_percent: float | None
    recent_range_ATR: float | None
    direction_changes: int | None
    range_width_ATR: float | None
    swing_count: int | None
    structure_change_frequency: float | None
    h1_trend: str
    h4_trend: str
    HTF_alignment: str
    regime_category: str
    range_like_candidate: bool
    diagnostic_tags: list[str] = field(default_factory=list)
    failure_flags: list[str] = field(default_factory=list)
    series_bars: int = 0
    history_bucket: str = ""
    data_quality: str = ""
    entry_index: int = 0
    MAE_R: float | None = None
    MFE_R: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_closed(row: Mapping[str, Any]) -> bool:
    return row.get("result") not in (None, "OPEN", "")


def _is_win(row: Mapping[str, Any]) -> bool:
    r = row.get("R")
    return r is not None and float(r) > 0


def _is_loss(row: Mapping[str, Any]) -> bool:
    r = row.get("R")
    return r is not None and float(r) <= 0


def _mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def enrich_chop_proxies(
    diag: TradeDiagRow,
    *,
    setup_candles: Sequence[Mapping[str, Any]],
    signal_config: SignalConfig,
) -> dict[str, Any]:
    """Extra pre-entry chop proxies; uses only candles through entry_index."""
    end = min(int(diag.entry_index), len(setup_candles) - 1)
    swings = swings_for_timeframe(
        setup_candles,
        signal_config,
        diag.timeframe,
        symbol=diag.symbol,
        as_of_index=end,
    )
    swing_count = len(swings)
    dchg = diag.direction_changes
    # Structure-change frequency: label-family flips per swing in trailing window.
    if swing_count > 0 and dchg is not None:
        scf = float(dchg) / float(swing_count)
    else:
        scf = None
    # range_width_ATR aliases recent_range_ATR (same pre-entry window definition).
    range_width = diag.recent_range_ATR
    return {
        "swing_count": swing_count,
        "structure_change_frequency": scf,
        "range_width_ATR": range_width,
    }


def research_diagnostic_tags(row: Mapping[str, Any]) -> list[str]:
    """Analytical tags only — may be multiple. Not production signals."""
    tags: list[str] = []
    regime = str(row.get("regime_category") or "")
    if regime == "ALIGNED_TREND":
        tags.append("ALIGNED")
    if regime == "HTF_CONFLICT":
        tags.append("HTF_CONFLICT")
    if regime == "LOCAL_ONLY":
        tags.append("LOCAL_ONLY")

    strength = row.get("trend_strength")
    dchg = row.get("direction_changes")
    rr = row.get("recent_range_ATR")
    try:
        strength_f = float(strength) if strength is not None else None
    except (TypeError, ValueError):
        strength_f = None
    try:
        dchg_i = int(dchg) if dchg is not None else None
    except (TypeError, ValueError):
        dchg_i = None
    try:
        rr_f = float(rr) if rr is not None else None
    except (TypeError, ValueError):
        rr_f = None

    rlc, _ = range_like_candidate(
        trend_strength=strength_f,
        direction_changes=dchg_i,
        recent_range_atr=rr_f,
    )
    possible_chop = bool(rlc) or (
        dchg_i is not None
        and rr_f is not None
        and strength_f is not None
        and dchg_i >= CHOP_DIR_CHANGES_MIN
        and rr_f <= CHOP_RANGE_ATR_MAX
        and strength_f < CHOP_STRENGTH_MAX
    )
    if possible_chop:
        tags.append("POSSIBLE_CHOP")

    clear = (
        regime in {"ALIGNED_TREND", "LOCAL_ONLY"}
        and strength_f is not None
        and dchg_i is not None
        and strength_f >= CLEAR_STRENGTH_MIN
        and dchg_i <= CLEAR_DIR_CHANGES_MAX
        and not possible_chop
        and regime != "HTF_CONFLICT"
    )
    if clear:
        tags.append("CLEAR_TREND")

    return tags


def failure_diagnostic_flags(row: Mapping[str, Any]) -> list[str]:
    """Descriptive flags on losses (or empty for winners). Multiple allowed."""
    if not _is_loss(row):
        return []
    flags: list[str] = []
    tags = set(row.get("diagnostic_tags") or research_diagnostic_tags(row))
    regime = str(row.get("regime_category") or "")
    if "HTF_CONFLICT" in tags or regime == "HTF_CONFLICT":
        flags.append("HTF_CONFLICT")
    if "LOCAL_ONLY" in tags or regime == "LOCAL_ONLY":
        flags.append("LOCAL_ONLY")
    if "POSSIBLE_CHOP" in tags:
        flags.append("POSSIBLE_CHOP")
    bos = row.get("BOS_ATR_distance")
    try:
        bos_f = float(bos) if bos is not None else None
    except (TypeError, ValueError):
        bos_f = None
    if bos_f is not None and bos_f < WEAK_BOS_ATR_MAX:
        flags.append("WEAK_BOS")
    if str(row.get("direction") or "").upper() == "SHORT":
        flags.append("DIRECTIONAL_SHORT")
    if not flags:
        flags.append("OTHER")
    return flags


def build_failure_row(
    diag: TradeDiagRow,
    *,
    setup_candles: Sequence[Mapping[str, Any]],
    signal_config: SignalConfig,
) -> FailureTradeRow:
    proxies = enrich_chop_proxies(
        diag, setup_candles=setup_candles, signal_config=signal_config
    )
    base = {
        "symbol": diag.symbol,
        "timeframe": diag.timeframe,
        "direction": diag.direction,
        "entry_time": diag.entry_time,
        "exit_time": diag.exit_time,
        "result": diag.result,
        "R": diag.R,
        "setup_trend": diag.setup_trend,
        "trend_strength": diag.trend_strength,
        "trend_duration_bars": diag.trend_duration_bars,
        "HH_HL_sequence_length": diag.HH_HL_sequence_length,
        "LH_LL_sequence_length": diag.LH_LL_sequence_length,
        "bos_direction": diag.bos_direction,
        "BOS_ATR_distance": diag.BOS_ATR_distance,
        "bars_since_BOS": diag.bars_since_BOS,
        "ATR_percent": diag.ATR_percent,
        "recent_range_ATR": diag.recent_range_ATR,
        "direction_changes": diag.direction_changes,
        "range_width_ATR": proxies["range_width_ATR"],
        "swing_count": proxies["swing_count"],
        "structure_change_frequency": proxies["structure_change_frequency"],
        "h1_trend": diag.h1_trend,
        "h4_trend": diag.h4_trend,
        "HTF_alignment": diag.HTF_alignment,
        "regime_category": diag.regime_category,
        "range_like_candidate": diag.range_like_candidate,
        "series_bars": diag.series_bars,
        "history_bucket": diag.history_bucket,
        "data_quality": diag.data_quality,
        "entry_index": diag.entry_index,
        "MAE_R": diag.MAE_R,
        "MFE_R": diag.MFE_R,
    }
    tags = research_diagnostic_tags(base)
    base["diagnostic_tags"] = tags
    base["failure_flags"] = failure_diagnostic_flags({**base, "diagnostic_tags": tags})
    return FailureTradeRow(**base)


def diagnose_bundle_trades(
    bundles: Sequence[Mapping[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
) -> list[FailureTradeRow]:
    """Attach research diagnostics to closed COMBO_02 trades (existing logic)."""
    scfg = signal_config or SignalConfig()
    out: list[FailureTradeRow] = []
    for bundle in bundles:
        sym = bundle["symbol"]
        tf = bundle["timeframe"]
        setup = bundle["setup_candles"]
        h1 = bundle.get("h1_candles") or []
        h4 = bundle.get("h4_candles") or []
        for t in bundle.get("trades") or []:
            if t.get("outcome") in (None, "OPEN"):
                continue
            ei = t.get("entry_index")
            if ei is None:
                continue
            diag = diagnose_at_entry(
                symbol=sym,
                timeframe=tf,
                entry_index=int(ei),
                setup_candles=setup,
                h1_candles=h1,
                h4_candles=h4,
                signal_config=scfg,
                trade=t,
            )
            out.append(
                build_failure_row(diag, setup_candles=setup, signal_config=scfg)
            )
    return out


def _rows_as_dicts(rows: Sequence[FailureTradeRow]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in rows]


def _overall_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    m = compute_metrics(rows)
    return {
        **m,
        "sample_size_flag": sample_size_label(int(m.get("n") or 0)),
    }


def win_loss_feature_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wins = [r for r in rows if _is_win(r)]
    losses = [r for r in rows if _is_loss(r)]
    out: dict[str, Any] = {}
    for key in FEATURE_KEYS:
        w_vals = [
            float(r[key])
            for r in wins
            if r.get(key) is not None and not (
                isinstance(r.get(key), float) and math.isnan(float(r[key]))
            )
        ]
        l_vals = [
            float(r[key])
            for r in losses
            if r.get(key) is not None and not (
                isinstance(r.get(key), float) and math.isnan(float(r[key]))
            )
        ]
        out[key] = {
            "winner_n": len(w_vals),
            "loser_n": len(l_vals),
            "winner_median": _median(w_vals),
            "loser_median": _median(l_vals),
            "winner_mean": _mean(w_vals),
            "loser_mean": _mean(l_vals),
            "sample_count_winners": len(w_vals),
            "sample_count_losers": len(l_vals),
            "note": "Descriptive only; no significance claim without a statistical test.",
        }
    return out


def _bucket_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
    buckets: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, pred in buckets:
        subset = [r for r in rows if pred(r.get(field))]
        m = compute_metrics(subset)
        n = int(m.get("n") or 0)
        losses = int(m.get("losses") or 0)
        result[name] = {
            "n": n,
            "wins": m.get("wins"),
            "losses": losses,
            "loss_rate": (losses / n) if n else None,
            "mean_R": m.get("mean_R"),
            "sample_size_flag": sample_size_label(n),
        }
    unmatched = [
        r
        for r in rows
        if _is_closed(r)
        and not any(pred(r.get(field)) for _, pred in buckets)
    ]
    if unmatched:
        m = compute_metrics(unmatched)
        n = int(m.get("n") or 0)
        result["UNAVAILABLE_OR_NULL"] = {
            "n": n,
            "wins": m.get("wins"),
            "losses": m.get("losses"),
            "loss_rate": (m["losses"] / n) if n and m.get("losses") is not None else None,
            "mean_R": m.get("mean_R"),
            "sample_size_flag": sample_size_label(n),
        }
    return result


def bucket_analyses(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def side(direction: str) -> list[dict[str, Any]]:
        return [r for r in rows if str(r.get("direction") or "").upper() == direction]

    def pack(subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "direction_changes": _bucket_report(
                subset, field="direction_changes", buckets=DIRECTION_CHANGE_BUCKETS
            ),
            "recent_range_ATR": _bucket_report(
                subset, field="recent_range_ATR", buckets=RECENT_RANGE_ATR_BUCKETS
            ),
            "trend_strength": _bucket_report(
                subset, field="trend_strength", buckets=TREND_STRENGTH_BUCKETS
            ),
            "BOS_ATR_distance": _bucket_report(
                subset, field="BOS_ATR_distance", buckets=BOS_ATR_DISTANCE_BUCKETS
            ),
        }

    return {
        "ALL": pack(rows),
        "LONG": pack(side("LONG")),
        "SHORT": pack(side("SHORT")),
        "bucket_definitions": {
            "direction_changes": ["0-2", "3-5", "6+"],
            "recent_range_ATR": ["<3", "3-5", ">5"],
            "trend_strength": ["<0.4", "0.4-0.7", ">0.7"],
            "BOS_ATR_distance": ["<0.5", "0.5-1", ">1"],
            "note": "Predeclared descriptive buckets — NOT optimized thresholds.",
        },
    }


def htf_state_key(row: Mapping[str, Any]) -> str:
    align = str(row.get("HTF_alignment") or "")
    if align == "ALIGNED" or align == "ALIGNED_PARTIAL":
        return "ALIGNED"
    if align == "CONFLICT":
        return "CONFLICT"
    if align in {"LOCAL_OR_NEUTRAL_HTF"}:
        return "LOCAL_OR_NEUTRAL_HTF"
    if align == "SETUP_NOT_TRENDING":
        return "SETUP_NOT_TRENDING"
    # Fall back to regime
    regime = str(row.get("regime_category") or "")
    if regime == "ALIGNED_TREND":
        return "ALIGNED"
    if regime == "HTF_CONFLICT":
        return "CONFLICT"
    if regime == "LOCAL_ONLY":
        return "LOCAL_OR_NEUTRAL_HTF"
    if regime == "NEUTRAL_STRUCTURE":
        return "SETUP_NOT_TRENDING"
    return "OTHER"


def cross_tab_tf_dir_htf_result(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tfs = ("15m", "1h")
    dirs = ("LONG", "SHORT")
    states = ("ALIGNED", "CONFLICT", "LOCAL_OR_NEUTRAL_HTF")
    results = ("WIN", "LOSS")
    out: dict[str, Any] = {}
    for tf in tfs:
        for d in dirs:
            for st in states:
                for res in results:
                    key = f"{tf} {d} {st} {res}"
                    subset = [
                        r
                        for r in rows
                        if str(r.get("timeframe") or "").lower() == tf
                        and str(r.get("direction") or "").upper() == d
                        and htf_state_key(r) == st
                        and (
                            (_is_win(r) and res == "WIN")
                            or (_is_loss(r) and res == "LOSS")
                        )
                    ]
                    m = compute_metrics(subset)
                    n = int(m.get("n") or 0)
                    out[key] = {
                        "n": n,
                        "mean_R": m.get("mean_R"),
                        "sample_size_flag": sample_size_label(n),
                    }
    # Aggregated TF×DIR×HTF without result split
    for tf in tfs:
        for d in dirs:
            for st in states:
                key = f"{tf} {d} {st}"
                subset = [
                    r
                    for r in rows
                    if str(r.get("timeframe") or "").lower() == tf
                    and str(r.get("direction") or "").upper() == d
                    and htf_state_key(r) == st
                ]
                m = compute_metrics(subset)
                n = int(m.get("n") or 0)
                losses = int(m.get("losses") or 0)
                out[key] = {
                    "n": n,
                    "wins": m.get("wins"),
                    "losses": losses,
                    "loss_rate": (losses / n) if n else None,
                    "mean_R": m.get("mean_R"),
                    "sample_size_flag": sample_size_label(n),
                }
    out["5m"] = {"status": "NO DATA", "n": 0}
    return out


def failure_flag_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [r for r in rows if _is_loss(r)]
    total = len(losers)
    flag_counts: dict[str, int] = defaultdict(int)
    for r in losers:
        for f in r.get("failure_flags") or []:
            flag_counts[str(f)] += 1
    by_flag: dict[str, Any] = {}
    for flag, count in sorted(flag_counts.items()):
        by_flag[flag] = {
            "loser_count_with_flag": count,
            "share_of_losers": (count / total) if total else None,
            "note": "Flags are non-exclusive; shares may sum > 100%.",
        }
    # Co-occurrence among losers
    combo_counts: dict[str, int] = defaultdict(int)
    for r in losers:
        flags = tuple(sorted(r.get("failure_flags") or []))
        if flags:
            combo_counts["+".join(flags)] += 1
    return {
        "total_losers": total,
        "by_flag": by_flag,
        "flag_combinations": dict(sorted(combo_counts.items(), key=lambda x: -x[1])),
        "definitions": {
            "HTF_CONFLICT": "regime_category/tag HTF_CONFLICT at entry",
            "LOCAL_ONLY": "regime_category/tag LOCAL_ONLY at entry",
            "POSSIBLE_CHOP": "research chop proxy at entry (not a production detector)",
            "WEAK_BOS": f"BOS_ATR_distance < {WEAK_BOS_ATR_MAX} (predeclared descriptive)",
            "DIRECTIONAL_SHORT": "trade direction == SHORT",
            "OTHER": "loss with none of the above flags",
        },
    }


def tag_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag in ("CLEAR_TREND", "POSSIBLE_CHOP", "HTF_CONFLICT", "LOCAL_ONLY", "ALIGNED"):
        subset = [r for r in rows if tag in (r.get("diagnostic_tags") or [])]
        m = compute_metrics(subset)
        n = int(m.get("n") or 0)
        losses = int(m.get("losses") or 0)
        out[tag] = {
            "n": n,
            "wins": m.get("wins"),
            "losses": losses,
            "loss_rate": (losses / n) if n else None,
            "mean_R": m.get("mean_R"),
            "sample_size_flag": sample_size_label(n),
            "LONG": _overall_block(
                [r for r in subset if str(r.get("direction") or "").upper() == "LONG"]
            ),
            "SHORT": _overall_block(
                [r for r in subset if str(r.get("direction") or "").upper() == "SHORT"]
            ),
        }
    return {
        "tags": out,
        "note": (
            "Research-only analytical tags. Not production signals. "
            "A trade may carry multiple tags."
        ),
        "possible_chop_rule": (
            f"RANGE_LIKE_CANDIDATE OR (direction_changes>={CHOP_DIR_CHANGES_MIN} "
            f"AND recent_range_ATR<={CHOP_RANGE_ATR_MAX} "
            f"AND trend_strength<{CHOP_STRENGTH_MAX})"
        ),
        "clear_trend_rule": (
            f"ALIGNED_TREND|LOCAL_ONLY AND trend_strength>={CLEAR_STRENGTH_MIN} "
            f"AND direction_changes<={CLEAR_DIR_CHANGES_MAX} AND not POSSIBLE_CHOP "
            f"AND not HTF_CONFLICT"
        ),
    }


def _split_dir_tf(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def filt(**kw: str) -> list[dict[str, Any]]:
        out = list(rows)
        if "direction" in kw:
            out = [r for r in out if str(r.get("direction") or "").upper() == kw["direction"]]
        if "timeframe" in kw:
            out = [
                r
                for r in out
                if str(r.get("timeframe") or "").lower() == kw["timeframe"].lower()
            ]
        return out

    return {
        "overall": _overall_block(rows),
        "LONG": {
            "overall": _overall_block(filt(direction="LONG")),
            "feature_stats": win_loss_feature_stats(filt(direction="LONG")),
            "buckets": bucket_analyses(filt(direction="LONG"))["LONG"],
            "failure_flags": failure_flag_summary(filt(direction="LONG")),
            "diagnostic_tags": tag_summary(filt(direction="LONG")),
        },
        "SHORT": {
            "overall": _overall_block(filt(direction="SHORT")),
            "feature_stats": win_loss_feature_stats(filt(direction="SHORT")),
            "buckets": bucket_analyses(filt(direction="SHORT"))["SHORT"],
            "failure_flags": failure_flag_summary(filt(direction="SHORT")),
            "diagnostic_tags": tag_summary(filt(direction="SHORT")),
        },
        "15m": {
            "overall": _overall_block(filt(timeframe="15m")),
            "LONG": _overall_block(filt(timeframe="15m", direction="LONG")),
            "SHORT": _overall_block(filt(timeframe="15m", direction="SHORT")),
            "feature_stats_LONG": win_loss_feature_stats(
                filt(timeframe="15m", direction="LONG")
            ),
            "feature_stats_SHORT": win_loss_feature_stats(
                filt(timeframe="15m", direction="SHORT")
            ),
            "buckets_LONG": bucket_analyses(filt(timeframe="15m", direction="LONG"))[
                "LONG"
            ],
            "buckets_SHORT": bucket_analyses(filt(timeframe="15m", direction="SHORT"))[
                "SHORT"
            ],
        },
        "1h": {
            "overall": _overall_block(filt(timeframe="1h")),
            "LONG": _overall_block(filt(timeframe="1h", direction="LONG")),
            "SHORT": _overall_block(filt(timeframe="1h", direction="SHORT")),
            "feature_stats_LONG": win_loss_feature_stats(
                filt(timeframe="1h", direction="LONG")
            ),
            "feature_stats_SHORT": win_loss_feature_stats(
                filt(timeframe="1h", direction="SHORT")
            ),
            "buckets_LONG": bucket_analyses(filt(timeframe="1h", direction="LONG"))[
                "LONG"
            ],
            "buckets_SHORT": bucket_analyses(filt(timeframe="1h", direction="SHORT"))[
                "SHORT"
            ],
        },
        "5m": {"status": "NO DATA", "n": 0},
    }


def factual_observations(report: Mapping[str, Any]) -> list[str]:
    obs: list[str] = []
    ds = report.get("dataset") or {}
    obs.append(
        f"Analyzed n={ds.get('n_trades')} closed COMBO_02 Path A trades "
        f"from full available PostgreSQL OHLCV "
        f"(symbols={ds.get('symbols')}, setup_bars_loaded={ds.get('bars_loaded')})."
    )
    overall = report.get("overall") or {}
    obs.append(
        f"Overall: wins={overall.get('wins')} losses={overall.get('losses')} "
        f"win_rate={overall.get('win_rate')} mean_R={overall.get('mean_R')} "
        f"{overall.get('sample_size_flag')}."
    )
    for side in ("LONG", "SHORT"):
        s = ((report.get("by_direction") or {}).get(side) or {}).get("overall") or {}
        obs.append(
            f"{side}: n={s.get('n')} wins={s.get('wins')} losses={s.get('losses')} "
            f"win_rate={s.get('win_rate')} mean_R={s.get('mean_R')} "
            f"{s.get('sample_size_flag')}."
        )
    flags = report.get("failure_diagnostic_flags") or {}
    total_losers = flags.get("total_losers")
    for flag, block in (flags.get("by_flag") or {}).items():
        obs.append(
            f"Among losers, flag {flag} appeared on "
            f"{block.get('loser_count_with_flag')}/{total_losers} "
            f"(share={block.get('share_of_losers')}); non-exclusive."
        )
    obs.append("5m: NO DATA if unavailable in this run.")
    obs.append(
        "Bucket and feature comparisons are descriptive only; "
        "no characteristic is ranked or selected as a filter."
    )
    obs.append(
        "MULTIPLE_TESTING_RISK: many predeclared slices are reported on one sample."
    )
    return obs


def build_report(
    rows: Sequence[FailureTradeRow],
    *,
    bars_loaded: Mapping[str, Any] | None = None,
    meta_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dicts = _rows_as_dicts(rows)
    closed = [r for r in dicts if _is_closed(r)]
    splits = _split_dir_tf(closed)
    buckets = bucket_analyses(closed)
    report: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": DATASET_LABEL,
            "combo": COMBO_ID,
            "path": "Path A (Trend + BOS)",
            "research_only": True,
            "live_engine_unchanged": True,
            "production_thresholds_unchanged": True,
            "no_look_ahead": True,
            "no_parameter_optimization": True,
            "no_production_sideways_detector": True,
            "no_fabricated_data": True,
            "existing_trade_logic_unchanged": True,
            **(dict(meta_extra) if meta_extra else {}),
        },
        "dataset": {
            "n_trades": len(closed),
            "symbols": sorted({str(r.get("symbol") or "") for r in closed}),
            "timeframes_present": sorted(
                {str(r.get("timeframe") or "").lower() for r in closed}
            ),
            "directions_present": sorted(
                {str(r.get("direction") or "").upper() for r in closed}
            ),
            "5m_status": "NO DATA",
            "bars_loaded": bars_loaded or {},
            "result_counts": _count(closed, "result"),
            "regime_counts": _count(closed, "regime_category"),
            "history_buckets": _count(closed, "history_bucket"),
        },
        "method": {
            "trade_source": "run_combination_backtest / collect_combo02_trades (COMBO_02)",
            "diagnostics": "diagnose_at_entry + research-only chop proxies / tags",
            "features_timing": "all features use candles with index <= entry_index; HTF as_of <= entry_time",
            "buckets": "predeclared descriptive ranges; not optimized",
            "diagnostic_tags": [
                "CLEAR_TREND",
                "POSSIBLE_CHOP",
                "HTF_CONFLICT",
                "LOCAL_ONLY",
                "ALIGNED",
            ],
            "failure_flags": [
                "HTF_CONFLICT",
                "LOCAL_ONLY",
                "POSSIBLE_CHOP",
                "WEAK_BOS",
                "DIRECTIONAL_SHORT",
                "OTHER",
            ],
            "interpretation_rule": (
                "Do not claim causation. Report associations / observed loss rates only."
            ),
        },
        "overall": splits["overall"],
        "by_direction": {
            "LONG": splits["LONG"],
            "SHORT": splits["SHORT"],
        },
        "by_timeframe": {
            "15m": splits["15m"],
            "1h": splits["1h"],
            "5m": splits["5m"],
        },
        "htf_cross_tab": cross_tab_tf_dir_htf_result(closed),
        "buckets": buckets,
        "winner_vs_loser_feature_stats": {
            "ALL": win_loss_feature_stats(closed),
            "LONG": win_loss_feature_stats(
                [r for r in closed if str(r.get("direction") or "").upper() == "LONG"]
            ),
            "SHORT": win_loss_feature_stats(
                [r for r in closed if str(r.get("direction") or "").upper() == "SHORT"]
            ),
        },
        "diagnostic_tag_summary": tag_summary(closed),
        "failure_diagnostic_flags": failure_flag_summary(closed),
        "sample_size_warnings": _sample_size_warnings(splits, buckets),
        "limitations": [
            "COMBO_02 Path A only; not full live Path B.",
            "4h HTF history in DB may be short → many HTF_UNAVAILABLE on 4h.",
            "POSSIBLE_CHOP / CLEAR_TREND are research proxies, not validated detectors.",
            "Direction asymmetry confounds regime comparisons if sides are pooled.",
            "Predeclared buckets create MULTIPLE_TESTING_RISK across many slices.",
            "No fees re-simulated beyond existing backtest R values.",
            "Feature differences are descriptive; not called statistically significant.",
        ],
        "trade_rows": closed,
        "acceptance": {
            "live_engine_unchanged": True,
            "production_thresholds_unchanged": True,
            "no_fabricated_data": True,
            "no_look_ahead": True,
            "existing_trade_logic_unchanged": True,
            "long_short_separated": True,
            "tf_15m_1h_separated": True,
            "research_only_classifications": True,
            "no_parameter_optimization": True,
            "no_production_sideways_detector": True,
            "all_sample_sizes_reported": True,
        },
    }
    report["factual_observations"] = factual_observations(report)
    return report


def _count(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for r in rows:
        c[str(r.get(field) or "")] += 1
    return dict(c)


def _sample_size_warnings(splits: Mapping[str, Any], buckets: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    for label, block in (
        ("overall", splits.get("overall")),
        ("LONG", (splits.get("LONG") or {}).get("overall")),
        ("SHORT", (splits.get("SHORT") or {}).get("overall")),
        ("15m", (splits.get("15m") or {}).get("overall")),
        ("1h", (splits.get("1h") or {}).get("overall")),
    ):
        if not block:
            continue
        n = int(block.get("n") or 0)
        flag = block.get("sample_size_flag") or sample_size_label(n)
        if flag == SAMPLE_INSUFFICIENT:
            warnings.append(f"{label}: n={n} → {SAMPLE_INSUFFICIENT}")
    warnings.append(
        f"Rule: n < {SAMPLE_THRESHOLD} → {SAMPLE_INSUFFICIENT}; "
        f"n >= {SAMPLE_THRESHOLD} → {SAMPLE_OK}. "
        "SAMPLE_SIZE_OK does not imply statistical reliability."
    )
    return warnings


def _fmt(x: Any, digits: int = 3) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return str(x)
        return f"{x:.{digits}f}"
    return str(x)


def _fmt_pct(x: Any) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Historical Failure-Regime Decomposition (Research Only)")
    lines.append("")
    lines.append(f"Generated: `{report['meta']['generated_at']}`")
    lines.append(
        f"Dataset: {report['meta']['dataset']} · Combo: `{report['meta']['combo']}` · "
        f"{report['meta']['path']}"
    )
    lines.append("")
    lines.append(
        "> Research only. Live engines unchanged. No production filters. "
        "No parameter optimization. Associations are observational."
    )
    lines.append("")

    ds = report["dataset"]
    lines.append("## 1. Dataset")
    lines.append("")
    lines.append(f"- Closed trades: **{ds['n_trades']}**")
    lines.append(f"- Symbols: `{ds['symbols']}`")
    lines.append(f"- Timeframes: `{ds['timeframes_present']}` (5m: **{ds['5m_status']}**)")
    lines.append(f"- Directions: `{ds['directions_present']}`")
    lines.append(f"- Bars loaded: `{ds.get('bars_loaded')}`")
    lines.append(f"- Results: `{ds['result_counts']}`")
    lines.append(f"- Regime counts: `{ds['regime_counts']}`")
    lines.append("")

    lines.append("## 2. Method")
    lines.append("")
    for k, v in report["method"].items():
        lines.append(f"- **{k}**: `{v}`")
    lines.append("")

    def _overall_table(title: str, block: Mapping[str, Any]) -> None:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(
            f"- n={block.get('n')} wins={block.get('wins')} losses={block.get('losses')} "
            f"WR={_fmt_pct(block.get('win_rate'))} mean_R={_fmt(block.get('mean_R'))} "
            f"median_R={_fmt(block.get('median_R'))} PF={_fmt(block.get('profit_factor'))} "
            f"`{block.get('sample_size_flag')}`"
        )
        lines.append("")

    lines.append("## 3. Overall")
    lines.append("")
    _overall_table("All trades", report["overall"])

    lines.append("## 4. LONG")
    lines.append("")
    long = report["by_direction"]["LONG"]
    _overall_table("LONG overall", long["overall"])
    lines.append("Failure flags (LONG losers):")
    lines.append(f"`{long['failure_flags']}`")
    lines.append("")

    lines.append("## 5. SHORT")
    lines.append("")
    short = report["by_direction"]["SHORT"]
    _overall_table("SHORT overall", short["overall"])
    lines.append("Failure flags (SHORT losers):")
    lines.append(f"`{short['failure_flags']}`")
    lines.append("")

    lines.append("## 6. 15m")
    lines.append("")
    t15 = report["by_timeframe"]["15m"]
    _overall_table("15m overall", t15["overall"])
    _overall_table("15m LONG", t15["LONG"])
    _overall_table("15m SHORT", t15["SHORT"])

    lines.append("## 7. 1h")
    lines.append("")
    t1h = report["by_timeframe"]["1h"]
    _overall_table("1h overall", t1h["overall"])
    _overall_table("1h LONG", t1h["LONG"])
    _overall_table("1h SHORT", t1h["SHORT"])
    lines.append("### 5m")
    lines.append("")
    lines.append("**NO DATA**")
    lines.append("")

    lines.append("## 8. HTF cross-tab")
    lines.append("")
    lines.append("| Cell | n | wins | losses | loss rate | mean R | sample |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for key, cell in report["htf_cross_tab"].items():
        if key == "5m":
            lines.append("| 5m | 0 | — | — | — | — | NO DATA |")
            continue
        if key.endswith(" WIN") or key.endswith(" LOSS"):
            lines.append(
                f"| {key} | {cell.get('n')} | — | — | — | {_fmt(cell.get('mean_R'))} | "
                f"{cell.get('sample_size_flag')} |"
            )
        else:
            lines.append(
                f"| {key} | {cell.get('n')} | {cell.get('wins')} | {cell.get('losses')} | "
                f"{_fmt_pct(cell.get('loss_rate'))} | {_fmt(cell.get('mean_R'))} | "
                f"{cell.get('sample_size_flag')} |"
            )
    lines.append("")

    def _bucket_section(num: str, title: str, field: str) -> None:
        lines.append(f"## {num}. {title}")
        lines.append("")
        lines.append("Predeclared descriptive buckets — not optimized.")
        lines.append("")
        for side in ("LONG", "SHORT"):
            lines.append(f"### {side}")
            lines.append("")
            lines.append("| Bucket | n | wins | losses | loss rate | mean R | sample |")
            lines.append("|---|---:|---:|---:|---:|---:|---|")
            block = ((report["buckets"].get(side) or {}).get(field) or {})
            for bname, b in block.items():
                lines.append(
                    f"| {bname} | {b.get('n')} | {b.get('wins')} | {b.get('losses')} | "
                    f"{_fmt_pct(b.get('loss_rate'))} | {_fmt(b.get('mean_R'))} | "
                    f"{b.get('sample_size_flag')} |"
                )
            lines.append("")

    _bucket_section("9", "Trend-strength buckets", "trend_strength")
    _bucket_section("10", "Direction-change buckets", "direction_changes")
    _bucket_section("11", "Range/ATR buckets", "recent_range_ATR")
    _bucket_section("12", "BOS-distance buckets", "BOS_ATR_distance")

    lines.append("## 13. Winner vs loser feature statistics")
    lines.append("")
    lines.append(
        "Descriptive means/medians only. Differences are **not** called significant."
    )
    lines.append("")
    for side in ("LONG", "SHORT", "ALL"):
        lines.append(f"### {side}")
        lines.append("")
        lines.append(
            "| Feature | win n | lose n | win median | lose median | win mean | lose mean |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        stats = (report["winner_vs_loser_feature_stats"] or {}).get(side) or {}
        for feat, st in stats.items():
            lines.append(
                f"| {feat} | {st.get('winner_n')} | {st.get('loser_n')} | "
                f"{_fmt(st.get('winner_median'))} | {_fmt(st.get('loser_median'))} | "
                f"{_fmt(st.get('winner_mean'))} | {_fmt(st.get('loser_mean'))} |"
            )
        lines.append("")

    lines.append("## 14. Failure diagnostic flags")
    lines.append("")
    lines.append(f"Total losers: **{(report['failure_diagnostic_flags'] or {}).get('total_losers')}**")
    lines.append("")
    lines.append("| Flag | loser count | share of losers |")
    lines.append("|---|---:|---:|")
    for flag, block in ((report["failure_diagnostic_flags"] or {}).get("by_flag") or {}).items():
        lines.append(
            f"| {flag} | {block.get('loser_count_with_flag')} | "
            f"{_fmt_pct(block.get('share_of_losers'))} |"
        )
    lines.append("")
    lines.append("Flag combinations:")
    lines.append(f"`{(report['failure_diagnostic_flags'] or {}).get('flag_combinations')}`")
    lines.append("")
    lines.append("Diagnostic tag summary:")
    lines.append(f"`{report.get('diagnostic_tag_summary')}`")
    lines.append("")

    lines.append("## 15. Sample-size warnings")
    lines.append("")
    for w in report.get("sample_size_warnings") or []:
        lines.append(f"- {w}")
    lines.append("")

    lines.append("## 16. Limitations")
    lines.append("")
    for lim in report.get("limitations") or []:
        lines.append(f"- {lim}")
    lines.append("")

    lines.append("## Factual observations")
    lines.append("")
    for o in report.get("factual_observations") or []:
        lines.append(f"- {o}")
    lines.append("")

    lines.append("## Acceptance")
    lines.append("")
    for k, v in (report.get("acceptance") or {}).items():
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: Mapping[str, Any], *, out_json: Path, out_md: Path) -> None:
    # Keep trade_rows in JSON; markdown references aggregates.
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
