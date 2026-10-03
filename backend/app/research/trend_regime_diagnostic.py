"""Research-only trend/regime diagnostics for closed COMBO_02 trades.

Does NOT modify live signal logic, thresholds, BOS/trend definitions, or entries.
All classifications use candles with index <= entry bar only (no look-ahead).
Regime categories are NOT derived from trade outcomes.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import swings_for_timeframe

DATASET_LABEL = "BOS Combination Research"
COMBO_ID = "COMBO_02"
PRIMARY_CATEGORIES = (
    "ALIGNED_TREND",
    "HTF_CONFLICT",
    "LOCAL_ONLY",
    "NEUTRAL_STRUCTURE",
)

# Research-only RANGE_LIKE_CANDIDATE rule (pre-entry). Not a production filter.
# Flags candidate when local structure is weakly trending AND swing labels alternate
# often AND recent range is compact vs ATR.
RANGE_LIKE_WINDOW = 40
RANGE_LIKE_MIN_DIRECTION_CHANGES = 4
RANGE_LIKE_MAX_STRENGTH = 0.55
RANGE_LIKE_MAX_RANGE_ATR = 3.5


@dataclass
class TradeDiagRow:
    symbol: str
    timeframe: str
    direction: str
    entry_time: str | None
    exit_time: str | None
    entry_price: float | None
    exit_price: float | None
    result: str | None
    R: float | None
    MAE: float | None
    MFE: float | None
    MAE_R: float | None
    MFE_R: float | None
    setup_trend: str
    h1_trend: str
    h4_trend: str
    regime_category: str
    range_like_candidate: bool
    range_like_rule: str
    trend_strength: float | None
    trend_duration_bars: int | None
    bars_since_BOS: int | None
    BOS_ATR_distance: float | None
    ATR_percent: float | None
    recent_range_ATR: float | None
    direction_changes: int | None
    HH_HL_sequence_length: int | None
    LH_LL_sequence_length: int | None
    HTF_alignment: str
    bos_direction: str | None
    bos_body_ratio: float | None
    impulse_state: str | None
    series_bars: int
    history_bucket: str
    data_quality: str
    entry_index: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def history_bucket(n: int) -> str:
    if n < 300:
        return "<300"
    if n < 1000:
        return "300-999"
    if n < 5000:
        return "1000-4999"
    return "5000+"


def _trend_label(trend: Mapping[str, Any] | None) -> str:
    if not trend:
        return "HTF_UNAVAILABLE"
    t = str(trend.get("trend") or "").upper()
    if t in {"BULLISH", "BEARISH", "NEUTRAL", "INSUFFICIENT_DATA", "WAITING"}:
        if t in {"WAITING", "INSUFFICIENT_DATA"}:
            return "HTF_UNAVAILABLE" if t == "WAITING" else t
        return t
    return "HTF_UNAVAILABLE"


def _side_of_trend(label: str) -> str | None:
    if label == "BULLISH":
        return "UP"
    if label == "BEARISH":
        return "DOWN"
    return None


def classify_regime_category(
    setup_trend: str,
    h1_trend: str,
    h4_trend: str,
    *,
    setup_tf: str,
) -> tuple[str, str]:
    """Return (regime_category, HTF_alignment). Research-only."""
    setup_side = _side_of_trend(setup_trend)
    if setup_side is None:
        return "NEUTRAL_STRUCTURE", "SETUP_NOT_TRENDING"

    htf_labels: list[str] = []
    tf = setup_tf.lower()
    if tf in {"5m", "15m", "1m"}:
        htf_labels = [h1_trend, h4_trend]
    elif tf == "1h":
        htf_labels = [h4_trend]
    else:
        htf_labels = [h1_trend, h4_trend]

    available = [x for x in htf_labels if x not in {"HTF_UNAVAILABLE", ""}]
    opposite = False
    agree = 0
    neutralish = 0
    for lab in available:
        side = _side_of_trend(lab)
        if side is None:
            if lab in {"NEUTRAL", "INSUFFICIENT_DATA"}:
                neutralish += 1
            continue
        if side == setup_side:
            agree += 1
        else:
            opposite = True

    if opposite:
        return "HTF_CONFLICT", "CONFLICT"
    if agree > 0 and not opposite:
        # At least one HTF agrees and none conflict
        if all(_side_of_trend(x) == setup_side for x in available if _side_of_trend(x)):
            if available and agree == len([x for x in available if _side_of_trend(x) is not None]):
                return "ALIGNED_TREND", "ALIGNED"
        return "ALIGNED_TREND", "ALIGNED_PARTIAL"
    # No opposite, no agreeing directional HTF → local only
    return "LOCAL_ONLY", "LOCAL_OR_NEUTRAL_HTF"


def as_of_index_at_or_before(
    candles: Sequence[Mapping[str, Any]],
    entry_ts: datetime | None,
) -> int | None:
    if not candles or entry_ts is None:
        return None
    if entry_ts.tzinfo is None:
        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
    best: int | None = None
    for i, c in enumerate(candles):
        t = candle_time(c)
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t <= entry_ts:
            best = i
        else:
            break
    return best


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _direction_changes(labels: Sequence[str]) -> int:
    """Count bullish-family ↔ bearish-family flips in swing label sequence."""
    def fam(lab: str) -> str | None:
        if lab in {"HH", "HL"}:
            return "BULL"
        if lab in {"LH", "LL"}:
            return "BEAR"
        return None

    changes = 0
    prev: str | None = None
    for lab in labels:
        f = fam(lab)
        if f is None:
            continue
        if prev is not None and f != prev:
            changes += 1
        prev = f
    return changes


def _sequence_lengths(labels: Sequence[str]) -> tuple[int, int]:
    """Trailing HH/HL run length and trailing LH/LL run length."""
    hh_hl = 0
    for lab in reversed(labels):
        if lab in {"HH", "HL"}:
            hh_hl += 1
        else:
            break
    lh_ll = 0
    for lab in reversed(labels):
        if lab in {"LH", "LL"}:
            lh_ll += 1
        else:
            break
    return hh_hl, lh_ll


def _trend_duration_bars(swings: Sequence[Any], entry_index: int) -> int | None:
    """Bars from earliest swing in trailing same-family sequence to entry."""
    if not swings:
        return None
    labels = [s.label for s in swings if getattr(s, "label", None)]
    if not labels:
        return None
    last = labels[-1]
    family = {"HH", "HL"} if last in {"HH", "HL"} else {"LH", "LL"} if last in {"LH", "LL"} else None
    if family is None:
        return None
    start_idx = None
    for s in reversed(list(swings)):
        if s.label in family:
            start_idx = s.bar_index
        else:
            break
    if start_idx is None:
        return None
    return max(0, entry_index - int(start_idx))


def range_like_candidate(
    *,
    trend_strength: float | None,
    direction_changes: int | None,
    recent_range_atr: float | None,
) -> tuple[bool, str]:
    rule = (
        f"RANGE_LIKE_CANDIDATE if trend_strength<{RANGE_LIKE_MAX_STRENGTH} "
        f"AND direction_changes>={RANGE_LIKE_MIN_DIRECTION_CHANGES} "
        f"AND recent_range_ATR<={RANGE_LIKE_MAX_RANGE_ATR} "
        f"(window={RANGE_LIKE_WINDOW} bars, pre-entry only)"
    )
    if trend_strength is None or direction_changes is None or recent_range_atr is None:
        return False, rule
    ok = (
        trend_strength < RANGE_LIKE_MAX_STRENGTH
        and direction_changes >= RANGE_LIKE_MIN_DIRECTION_CHANGES
        and recent_range_atr <= RANGE_LIKE_MAX_RANGE_ATR
    )
    return ok, rule


def diagnose_at_entry(
    *,
    symbol: str,
    timeframe: str,
    entry_index: int,
    setup_candles: Sequence[Mapping[str, Any]],
    h1_candles: Sequence[Mapping[str, Any]],
    h4_candles: Sequence[Mapping[str, Any]],
    signal_config: SignalConfig,
    trade: Mapping[str, Any],
) -> TradeDiagRow:
    """Classify regime using only setup_candles[:entry_index+1] and HTF <= entry time."""
    engine = SignalEngine(signal_config)
    end = min(entry_index, len(setup_candles) - 1)
    window = list(setup_candles[: end + 1])
    tf_a = engine.analyze_timeframe(
        symbol, timeframe, setup_candles, as_of_index=end
    )
    setup_trend_obj = tf_a.get("trend") or {}
    setup_trend = _trend_label(setup_trend_obj)
    if setup_trend == "HTF_UNAVAILABLE":
        # WAITING on setup TF → treat as insufficient, not HTF
        setup_trend = str(setup_trend_obj.get("trend") or "INSUFFICIENT_DATA").upper()
        if setup_trend == "WAITING":
            setup_trend = "INSUFFICIENT_DATA"

    entry_ts = candle_time(setup_candles[end]) if end >= 0 else _parse_iso(trade.get("signal_time"))
    h1_i = as_of_index_at_or_before(h1_candles, entry_ts)
    h4_i = as_of_index_at_or_before(h4_candles, entry_ts)

    if h1_i is None or not h1_candles:
        h1_trend = "HTF_UNAVAILABLE"
    else:
        h1_a = engine.analyze_timeframe(symbol, "1h", h1_candles, as_of_index=h1_i)
        h1_trend = _trend_label(h1_a.get("trend"))

    if h4_i is None or not h4_candles:
        h4_trend = "HTF_UNAVAILABLE"
    else:
        h4_a = engine.analyze_timeframe(symbol, "4h", h4_candles, as_of_index=h4_i)
        h4_trend = _trend_label(h4_a.get("trend"))

    # For 1h setup, 1h_trend column = setup trend (same TF), 4h is HTF
    if timeframe.lower() == "1h":
        h1_trend_col = setup_trend
    else:
        h1_trend_col = h1_trend

    regime, htf_align = classify_regime_category(
        setup_trend, h1_trend_col, h4_trend, setup_tf=timeframe
    )

    swings = swings_for_timeframe(
        setup_candles, signal_config, timeframe, symbol=symbol, as_of_index=end
    )
    labels = [s.label for s in swings if s.label][-12:]
    dchg = _direction_changes(labels)
    hh_hl_len, lh_ll_len = _sequence_lengths(labels)
    dur = _trend_duration_bars(swings, end)

    _, highs, lows, closes, _ = series_ohlcv(window)
    atr_period = signal_config.atr_period
    atr_v = calc_atr(highs, lows, closes, atr_period) if len(closes) >= atr_period else None
    close = closes[-1] if closes else None
    atr_pct = (atr_v / close) if atr_v and close else None
    w = min(RANGE_LIKE_WINDOW, len(highs))
    recent_range_atr = None
    if atr_v and atr_v > 0 and w >= 2:
        recent_range_atr = (max(highs[-w:]) - min(lows[-w:])) / atr_v

    bos = tf_a.get("bos") or {}
    bos_dir = bos.get("direction")
    bos_level = bos.get("broken_level")
    bos_idx = bos.get("break_index")
    bars_since_bos = None
    if isinstance(bos_idx, int):
        bars_since_bos = max(0, end - bos_idx)
    bos_atr_dist = None
    if bos_level is not None and atr_v and atr_v > 0 and close is not None:
        bos_atr_dist = abs(float(close) - float(bos_level)) / atr_v

    # BOS candle body ratio at break bar (pre-entry)
    bos_body = None
    if isinstance(bos_idx, int) and 0 <= bos_idx <= end:
        o, h, l, c, _ = ohlc(setup_candles, bos_idx)
        span = h - l
        if span > 0:
            bos_body = abs(c - o) / span

    impulse = tf_a.get("impulse") or {}
    strength = setup_trend_obj.get("trend_strength")
    try:
        strength_f = float(strength) if strength is not None else None
    except (TypeError, ValueError):
        strength_f = None

    rlc, rlc_rule = range_like_candidate(
        trend_strength=strength_f,
        direction_changes=dchg,
        recent_range_atr=recent_range_atr,
    )

    series_n = len(setup_candles)
    dq = "OK"
    if series_n < 300:
        dq = "SHALLOW_HISTORY"
    if h1_trend == "HTF_UNAVAILABLE" and h4_trend == "HTF_UNAVAILABLE" and timeframe.lower() != "1h":
        dq = "HTF_MISSING" if dq == "OK" else f"{dq}+HTF_MISSING"

    return TradeDiagRow(
        symbol=symbol.upper(),
        timeframe=timeframe,
        direction=str(trade.get("direction") or ""),
        entry_time=trade.get("signal_time"),
        exit_time=trade.get("exit_time"),
        entry_price=trade.get("entry_price"),
        exit_price=trade.get("exit_price"),
        result=trade.get("outcome"),
        R=trade.get("r_multiple"),
        MAE=trade.get("mae"),
        MFE=trade.get("mfe"),
        MAE_R=trade.get("mae_r"),
        MFE_R=trade.get("mfe_r"),
        setup_trend=setup_trend,
        h1_trend=h1_trend_col,
        h4_trend=h4_trend,
        regime_category=regime,
        range_like_candidate=rlc,
        range_like_rule=rlc_rule,
        trend_strength=strength_f,
        trend_duration_bars=dur,
        bars_since_BOS=bars_since_bos,
        BOS_ATR_distance=bos_atr_dist,
        ATR_percent=atr_pct,
        recent_range_ATR=recent_range_atr,
        direction_changes=dchg,
        HH_HL_sequence_length=hh_hl_len,
        LH_LL_sequence_length=lh_ll_len,
        HTF_alignment=htf_align,
        bos_direction=str(bos_dir) if bos_dir else None,
        bos_body_ratio=bos_body,
        impulse_state=str(impulse.get("impulse_state") or impulse.get("state") or None),
        series_bars=series_n,
        history_bucket=history_bucket(series_n),
        data_quality=dq,
        entry_index=end,
    )


def summarize_group(rows: Sequence[TradeDiagRow]) -> dict[str, Any]:
    closed = [r for r in rows if r.result not in (None, "OPEN")]
    n = len(closed)
    if n == 0:
        return {"sample_size": 0, "insufficient_sample": True}
    rs = [float(r.R) for r in closed if r.R is not None]
    wins = [r for r in closed if r.R is not None and float(r.R) > 0]
    losses = [r for r in closed if r.R is not None and float(r.R) <= 0]
    mae = [float(r.MAE_R) for r in closed if r.MAE_R is not None]
    mfe = [float(r.MFE_R) for r in closed if r.MFE_R is not None]
    tp1 = sum(1 for r in closed if str(r.result).startswith("TP"))
    sl = sum(1 for r in closed if r.result == "SL")
    mean_r = sum(rs) / len(rs) if rs else None
    med_r = sorted(rs)[len(rs) // 2] if rs else None
    gp = sum(x for x in rs if x > 0)
    gl = abs(sum(x for x in rs if x <= 0))
    pf = (gp / gl) if gl > 0 else None
    out = {
        "sample_size": n,
        "insufficient_sample": n < 30,
        "winners": len(wins),
        "losers": len(losses),
        "win_rate": len(wins) / n if n else None,
        "SL_rate": sl / n if n else None,
        "TP1_rate": tp1 / n if n else None,
        "mean_R": mean_r,
        "median_R": med_r,
        "profit_factor": pf,
        "avg_MAE_R": sum(mae) / len(mae) if mae else None,
        "avg_MFE_R": sum(mfe) / len(mfe) if mfe else None,
        "mean_R_bootstrap_95ci": bootstrap_mean_ci(rs) if len(rs) >= 10 else None,
    }
    return out


def bootstrap_mean_ci(
    values: Sequence[float], *, n_boot: int = 1000, alpha: float = 0.05, seed: int = 42
) -> dict[str, float] | None:
    if len(values) < 10:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    n = len(values)
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return {"low": lo, "high": hi, "mean": sum(values) / n}


def htf_conflict_buckets(rows: Sequence[TradeDiagRow]) -> dict[str, dict[str, Any]]:
    """Setup vs HTF directional matrix (research)."""
    buckets: dict[str, list[TradeDiagRow]] = defaultdict(list)
    for r in rows:
        setup = r.setup_trend
        # Prefer conflict-relevant HTF: for 15m/5m use 1h then 4h; for 1h use 4h
        if r.timeframe.lower() == "1h":
            htf = r.h4_trend
        else:
            htf = r.h1_trend if r.h1_trend != "HTF_UNAVAILABLE" else r.h4_trend
        key = f"setup_{setup}__htf_{htf}"
        buckets[key].append(r)
    return {k: summarize_group(v) for k, v in sorted(buckets.items())}


def current_trend_engine_audit() -> dict[str, Any]:
    """Static documentation of production trend rules (read-only description)."""
    return {
        "modules": {
            "swing": "app/signals/swing_detector.py",
            "trend": "app/signals/trend_engine.py",
            "setup_tf_analysis": "app/signals/signal_engine.py::analyze_timeframe",
            "research_combo": "app/research/combination_engine.py",
            "backtest": "app/research/combination_backtest.py",
        },
        "swing_defaults": {
            "15m_5m_1m": {"left": 2, "right": 2},
            "1h_4h_1d": {"left": 3, "right": 3},
            "atr_period": 14,
            "minimum_swing_distance_atr_default": 0.0,
            "price_source": "candle high for swing highs; candle low for swing lows (wicks)",
            "confirmation": "swing at i confirmed only after right bars closed (no look-ahead)",
        },
        "labels": {
            "HH": "swing high price > previous swing high",
            "LH": "swing high price <= previous swing high",
            "HL": "swing low price > previous swing low",
            "LL": "swing low price <= previous swing low",
        },
        "trend_rules": {
            "BULLISH": "last high label HH AND last low label HL AND h2.price>h1.price AND l2.price>l1.price",
            "BEARISH": "last high label LH AND last low label LL AND h2.price<h1.price AND l2.price<l1.price",
            "NEUTRAL": "enough swings but neither bullish nor bearish pattern",
            "INSUFFICIENT_DATA": "<2 confirmed highs or <2 confirmed lows",
            "local_only": True,
            "mtf_in_trend_engine": False,
            "uses_close_color": False,
            "uses_volume": False,
            "uses_adx": False,
            "uses_range_width": False,
            "atr_in_trend_label": False,
            "atr_in_swing_filter_optional": True,
            "trend_strength": "derived from relative HH/HL or LH/LL spacing; floor 0.3 when trending, 0.1 NEUTRAL",
        },
        "sideways_detector": None,
        "sideways_statement": (
            "Current engine has trend classification but no independent "
            "sideways-regime classification."
        ),
        "volume_regime_note": (
            "volume_engine.volume_regime exists for RVOL banding only — "
            "not a price sideways/chop detector."
        ),
        "research_regime_note": (
            "CombinationResult.regime is hardcoded REGIME_NOT_AVAILABLE in research metrics."
        ),
    }


def run_diagnostics_on_trades(
    trade_bundles: Sequence[dict[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
) -> list[TradeDiagRow]:
    """trade_bundles items: symbol, timeframe, setup_candles, h1, h4, trades(list of dicts)."""
    scfg = signal_config or SignalConfig()
    rows: list[TradeDiagRow] = []
    for bundle in trade_bundles:
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
            rows.append(
                diagnose_at_entry(
                    symbol=sym,
                    timeframe=tf,
                    entry_index=int(ei),
                    setup_candles=setup,
                    h1_candles=h1,
                    h4_candles=h4,
                    signal_config=scfg,
                    trade=t,
                )
            )
    return rows


def collect_combo02_trades(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    direction: str | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    combo = get_combination(COMBO_ID)
    assert combo is not None
    out = run_combination_backtest(
        symbol,
        timeframe,
        candles,
        combo,
        signal_config=signal_config,
        research_config=research_config,
        direction_filter=direction,
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    return list(out.get("trades") or [])


def build_report_payload(rows: Sequence[TradeDiagRow]) -> dict[str, Any]:
    audit = current_trend_engine_audit()
    by_regime: dict[str, list[TradeDiagRow]] = defaultdict(list)
    for r in rows:
        by_regime[r.regime_category].append(r)

    winners = [r for r in rows if r.R is not None and float(r.R) > 0]
    losers = [r for r in rows if r.R is not None and float(r.R) <= 0]

    def _regime_table(subset: Sequence[TradeDiagRow]) -> dict[str, Any]:
        return {cat: summarize_group(by_regime.get(cat, [])) for cat in PRIMARY_CATEGORIES}

    # Winner/loser share by regime
    def _share(subset: Sequence[TradeDiagRow]) -> dict[str, Any]:
        n = len(subset)
        counts: dict[str, int] = defaultdict(int)
        for r in subset:
            counts[r.regime_category] += 1
        return {
            "n": n,
            "by_regime": {
                k: {
                    "count": counts.get(k, 0),
                    "pct": (counts.get(k, 0) / n * 100) if n else None,
                }
                for k in PRIMARY_CATEGORIES
            },
            "range_like_candidate_count": sum(1 for r in subset if r.range_like_candidate),
            "range_like_candidate_pct": (
                sum(1 for r in subset if r.range_like_candidate) / n * 100 if n else None
            ),
        }

    tf_break: dict[str, Any] = {}
    for tf in ("5m", "15m", "1h"):
        sub = [r for r in rows if r.timeframe.lower() == tf]
        tf_break[tf] = {
            "overall": summarize_group(sub),
            "by_regime": {
                cat: summarize_group([r for r in sub if r.regime_category == cat])
                for cat in PRIMARY_CATEGORIES
            },
            "by_direction": {
                d: summarize_group([r for r in sub if r.direction == d])
                for d in ("LONG", "SHORT")
            },
        }

    dir_break = {
        d: {
            "overall": summarize_group([r for r in rows if r.direction == d]),
            "by_regime": {
                cat: summarize_group(
                    [r for r in rows if r.direction == d and r.regime_category == cat]
                )
                for cat in PRIMARY_CATEGORIES
            },
            "by_tf": {
                tf: summarize_group(
                    [r for r in rows if r.direction == d and r.timeframe.lower() == tf]
                )
                for tf in ("5m", "15m", "1h")
            },
        }
        for d in ("LONG", "SHORT")
    }

    hist = {
        b: summarize_group([r for r in rows if r.history_bucket == b])
        for b in ("<300", "300-999", "1000-4999", "5000+")
    }

    rlc_rows = [r for r in rows if r.range_like_candidate]
    bos_q = {
        "avg_bos_atr_distance_winners": _avg(
            [r.BOS_ATR_distance for r in winners if r.BOS_ATR_distance is not None]
        ),
        "avg_bos_atr_distance_losers": _avg(
            [r.BOS_ATR_distance for r in losers if r.BOS_ATR_distance is not None]
        ),
        "avg_bos_body_ratio_winners": _avg(
            [r.bos_body_ratio for r in winners if r.bos_body_ratio is not None]
        ),
        "avg_bos_body_ratio_losers": _avg(
            [r.bos_body_ratio for r in losers if r.bos_body_ratio is not None]
        ),
        "avg_bars_since_bos_winners": _avg(
            [float(r.bars_since_BOS) for r in winners if r.bars_since_BOS is not None]
        ),
        "avg_bars_since_bos_losers": _avg(
            [float(r.bars_since_BOS) for r in losers if r.bars_since_BOS is not None]
        ),
    }

    return {
        "label": "TREND_REGIME_DIAGNOSTIC",
        "dataset": DATASET_LABEL,
        "combination_id": COMBO_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sideways_detector": audit["sideways_statement"],
        "RANGE_LIKE_primary_category": "NOT_AVAILABLE",
        "RANGE_LIKE_note": (
            "No validated production sideways metric; primary category RANGE_LIKE "
            "is NOT_AVAILABLE. Separate RANGE_LIKE_CANDIDATE flag uses research-only rule."
        ),
        "trend_engine_audit": audit,
        "n_trades": len(rows),
        "overall": summarize_group(rows),
        "regime_counts": {cat: len(by_regime.get(cat, [])) for cat in PRIMARY_CATEGORIES},
        "by_regime": {cat: summarize_group(by_regime.get(cat, [])) for cat in PRIMARY_CATEGORIES},
        "winners": _share(winners),
        "losers": _share(losers),
        "winner_stats": summarize_group(winners),
        "loser_stats": summarize_group(losers),
        "timeframes": tf_break,
        "directions": dir_break,
        "htf_conflict_matrix": htf_conflict_buckets(rows),
        "bos_quality": bos_q,
        "range_like_candidate": {
            "rule": rlc_rows[0].range_like_rule if rlc_rows else range_like_candidate(
                trend_strength=None, direction_changes=None, recent_range_atr=None
            )[1],
            "count": len(rlc_rows),
            "stats": summarize_group(rlc_rows),
            "among_losers_pct": _share(losers)["range_like_candidate_pct"],
            "among_winners_pct": _share(winners)["range_like_candidate_pct"],
        },
        "historical_depth": hist,
        "acceptance": {
            "live_signal_logic_modified": False,
            "production_thresholds_modified": False,
            "trend_definitions_modified": False,
            "regime_uses_entry_only": True,
            "regime_uses_outcome": False,
            "missing_htf_as_bullish_bearish": False,
            "small_sample_flag_threshold": 30,
        },
    }


def _avg(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def render_markdown(payload: dict[str, Any], rows: Sequence[TradeDiagRow]) -> str:
    audit = payload["trend_engine_audit"]
    lines: list[str] = []
    lines.append("# Trend / Regime Diagnostic (Research Only)")
    lines.append("")
    lines.append(f"Generated: `{payload['generated_at']}`")
    lines.append(f"Dataset: {payload['dataset']} · Combo: `{payload['combination_id']}`")
    lines.append("")
    lines.append("> Diagnostic only. Not a profitability claim. Live engines unchanged.")
    lines.append("")
    lines.append("## 1. Current trend-engine definition")
    lines.append("")
    lines.append("```")
    lines.append(str(audit["trend_rules"]))
    lines.append("```")
    lines.append("")
    lines.append("Swing defaults:")
    lines.append(f"- {audit['swing_defaults']}")
    lines.append(f"- Labels: {audit['labels']}")
    lines.append("")
    lines.append("## 2. Whether a sideways detector exists")
    lines.append("")
    lines.append(payload["sideways_detector"])
    lines.append("")
    lines.append(f"Primary category `RANGE_LIKE`: **{payload['RANGE_LIKE_primary_category']}**")
    lines.append(payload["RANGE_LIKE_note"])
    lines.append("")
    lines.append("## 3. Local vs HTF classification")
    lines.append("")
    lines.append(
        "Setup trend from setup TF swings only. HTF trends from 1h/4h series "
        "at last bar with time ≤ entry time. Missing HTF → `HTF_UNAVAILABLE` "
        "(never treated as bullish/bearish)."
    )
    lines.append("")
    lines.append("## 4. Regime diagnostic methodology")
    lines.append("")
    lines.append(
        "- `ALIGNED_TREND`: setup trending and available directional HTF(s) agree, none opposite\n"
        "- `HTF_CONFLICT`: setup trending and ≥1 HTF opposite\n"
        "- `LOCAL_ONLY`: setup trending; HTF neutral/unavailable/no directional agreement\n"
        "- `NEUTRAL_STRUCTURE`: setup not BULLISH/BEARISH\n"
        "- `RANGE_LIKE_CANDIDATE`: separate research flag; see rule in JSON"
    )
    lines.append("")
    lines.append("## 5. Data quality")
    lines.append("")
    lines.append(f"Trades analyzed: **{payload['n_trades']}**")
    lines.append(f"Overall: `{payload['overall']}`")
    lines.append("")
    lines.append("## 6. Overall trade breakdown")
    lines.append("")
    lines.append(f"Regime counts: `{payload['regime_counts']}`")
    lines.append("")
    lines.append("| Category | n | mean R | win rate | SL rate | insufficient |")
    lines.append("|----------|---|--------|----------|---------|--------------|")
    for cat in PRIMARY_CATEGORIES:
        s = payload["by_regime"][cat]
        lines.append(
            f"| {cat} | {s.get('sample_size')} | {_fmt(s.get('mean_R'))} | "
            f"{_fmt(s.get('win_rate'))} | {_fmt(s.get('SL_rate'))} | "
            f"{s.get('insufficient_sample')} |"
        )
    lines.append("")
    lines.append("### Winners vs losers — regime share")
    lines.append("")
    lines.append(f"Winners: `{payload['winners']}`")
    lines.append(f"Losers: `{payload['losers']}`")
    lines.append("")
    for title, key in (("7. 5M analysis", "5m"), ("8. 15M analysis", "15m"), ("9. 1H analysis", "1h")):
        lines.append(f"## {title}")
        lines.append("")
        block = payload["timeframes"].get(key) or {}
        lines.append(f"Overall: `{block.get('overall')}`")
        lines.append(f"By regime: `{block.get('by_regime')}`")
        lines.append(f"By direction: `{block.get('by_direction')}`")
        lines.append("")
    lines.append("## 10. LONG analysis")
    lines.append("")
    lines.append(f"`{payload['directions']['LONG']}`")
    lines.append("")
    lines.append("## 11. SHORT analysis")
    lines.append("")
    lines.append(f"`{payload['directions']['SHORT']}`")
    lines.append("")
    lines.append("## 12. HTF conflict analysis")
    lines.append("")
    lines.append(f"`{payload['htf_conflict_matrix']}`")
    lines.append("")
    lines.append("## 13. BOS quality analysis")
    lines.append("")
    lines.append(f"`{payload['bos_quality']}`")
    lines.append("")
    lines.append("## 14. Range/chop diagnostic")
    lines.append("")
    lines.append(f"`{payload['range_like_candidate']}`")
    lines.append("")
    lines.append("## 15. Historical-depth analysis")
    lines.append("")
    lines.append(f"`{payload['historical_depth']}`")
    lines.append("")
    lines.append("## 16. Sample-size warnings")
    lines.append("")
    lines.append(
        "Any group with `sample_size < 30` is flagged `insufficient_sample` / "
        "INSUFFICIENT_SAMPLE — do not over-interpret."
    )
    lines.append("")
    lines.append("## 17. Limitations")
    lines.append("")
    lines.append(
        "- COMBO_02 Path A only (Trend+BOS); not full live Path B\n"
        "- 4h history in DB may be short → many HTF_UNAVAILABLE on 4h\n"
        "- RANGE_LIKE primary category NOT_AVAILABLE; candidate rule is research-only\n"
        "- No ADX/sideways production detector\n"
        "- Uneven history depth across series\n"
        "- Fees not re-simulated in this diagnostic (R from engine)"
    )
    lines.append("")
    lines.append("## Research observations")
    lines.append("")
    obs = _observations(payload, rows)
    for o in obs:
        lines.append(f"- {o}")
    lines.append("")
    lines.append("## Charts (diagnostic)")
    lines.append("")
    lines.append(_mermaid_regime_counts(payload))
    lines.append("")
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _observations(payload: dict[str, Any], rows: Sequence[TradeDiagRow]) -> list[str]:
    out: list[str] = []
    n = payload["n_trades"]
    out.append(f"Analyzed n={n} closed COMBO_02 trades.")
    losers = payload["losers"]
    ln = losers.get("n") or 0
    if ln:
        for cat in PRIMARY_CATEGORIES:
            c = losers["by_regime"][cat]["count"]
            pct = losers["by_regime"][cat]["pct"]
            out.append(
                f"{_fmt(pct)}% of losing trades ({c}/{ln}) occurred in {cat}."
            )
        out.append(
            f"{_fmt(losers.get('range_like_candidate_pct'))}% of losing trades "
            f"were RANGE_LIKE_CANDIDATE ({losers.get('range_like_candidate_count')})."
        )
    for cat, cnt in payload["regime_counts"].items():
        out.append(f"{cnt} trades were classified {cat}.")
    rlc = payload["range_like_candidate"]
    out.append(
        f"RANGE_LIKE_CANDIDATE had n={rlc['count']} "
        f"(insufficient_sample={rlc['stats'].get('insufficient_sample')})."
    )
    # Depth
    for b, s in payload["historical_depth"].items():
        out.append(
            f"History bucket {b}: n={s.get('sample_size')} "
            f"mean_R={_fmt(s.get('mean_R'))} insufficient={s.get('insufficient_sample')}."
        )
    return out


def _mermaid_regime_counts(payload: dict[str, Any]) -> str:
    lines = ["```mermaid", "pie title Trade count by regime category"]
    for cat, n in payload["regime_counts"].items():
        lines.append(f'    "{cat}" : {n}')
    lines.append("```")
    return "\n".join(lines)


def assert_no_future_influence(
    candles: Sequence[Mapping[str, Any]],
    entry_index: int,
    signal_config: SignalConfig,
    symbol: str = "TEST",
    timeframe: str = "15m",
) -> None:
    """Prove truncating future candles does not change setup trend at entry."""
    engine = SignalEngine(signal_config)
    full = engine.analyze_timeframe(
        symbol, timeframe, candles, as_of_index=entry_index
    )
    truncated = list(candles[: entry_index + 1])
    trunc = engine.analyze_timeframe(
        symbol, timeframe, truncated, as_of_index=len(truncated) - 1
    )
    assert (full.get("trend") or {}).get("trend") == (trunc.get("trend") or {}).get(
        "trend"
    )
    # Extra future bars must not change as_of result
    if entry_index + 5 < len(candles):
        longer = engine.analyze_timeframe(
            symbol, timeframe, candles, as_of_index=entry_index
        )
        assert (longer.get("trend") or {}).get("trend") == (full.get("trend") or {}).get(
            "trend"
        )
