"""Hypothesis tests H1–H14 — descriptive evidence only (research-only)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.trade_plan_forensics.aggregates import summarize
from app.research.trade_plan_forensics.schemas import sample_size_status
from app.research.trade_plan_forensics.thresholds import SL_TIGHT_ATR_MAX


def _r(rec: Mapping[str, Any]) -> float | None:
    try:
        v = (rec.get("trade") or {}).get("R")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _status(n: int, supported: bool | None) -> str:
    if n < 30 or supported is None:
        return "INSUFFICIENT_DATA"
    return "SUPPORTED" if supported else "NOT_SUPPORTED"


def _mean_r(recs: Sequence[Mapping[str, Any]]) -> float | None:
    rs = [x for x in (_r(r) for r in recs) if x is not None]
    return sum(rs) / len(rs) if rs else None


def evaluate_hypotheses(recs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ok = [r for r in recs if r.get("status") == "OK" and _r(r) is not None]
    overall = summarize(ok)

    def subset(pred):
        return [r for r in ok if pred(r)]

    out: list[dict[str, Any]] = []

    # H1 late after BOS
    late = subset(lambda r: (r.get("entry_timing") or {}).get("class") in ("LATE", "VERY_LATE"))
    timely = subset(lambda r: (r.get("entry_timing") or {}).get("class") in ("EARLY", "TIMELY"))
    m_late, m_timely = _mean_r(late), _mean_r(timely)
    supported = None
    if len(late) >= 30 and len(timely) >= 30 and m_late is not None and m_timely is not None:
        supported = m_late < m_timely
    out.append(
        {
            "id": "H1",
            "statement": "Entry happens too late after BOS.",
            "potential_issue": "Wrong entry timing (late after BOS)",
            "n_late": len(late),
            "n_timely": len(timely),
            "mean_R_late": m_late,
            "mean_R_timely": m_timely,
            "sample_status": sample_size_status(min(len(late), len(timely))),
            "status": _status(min(len(late), len(timely)), supported),
            "evidence": (
                f"mean_R LATE/VERY_LATE={m_late} vs EARLY/TIMELY={m_timely}; "
                f"n_late={len(late)} n_timely={len(timely)}"
            ),
        }
    )

    # H2 before retest confirmation
    no_retest = subset(
        lambda r: not ((r.get("local_structure") or {}).get("retest") or {}).get("retest")
    )
    with_retest = subset(
        lambda r: bool(((r.get("local_structure") or {}).get("retest") or {}).get("retest"))
    )
    m_nr, m_wr = _mean_r(no_retest), _mean_r(with_retest)
    supported = None
    if len(no_retest) >= 30 and m_nr is not None and m_wr is not None and len(with_retest) >= 10:
        supported = m_nr < m_wr
    elif len(with_retest) < 30:
        supported = None
    out.append(
        {
            "id": "H2",
            "statement": "Entry happens before proper retest confirmation.",
            "potential_issue": "Poor retest / entry before confirmation",
            "n_no_retest": len(no_retest),
            "n_with_retest": len(with_retest),
            "mean_R_no_retest": m_nr,
            "mean_R_with_retest": m_wr,
            "sample_status": sample_size_status(len(no_retest)),
            "status": _status(len(no_retest), supported) if with_retest else "INSUFFICIENT_DATA",
            "evidence": (
                f"mean_R no_retest={m_nr} (n={len(no_retest)}) vs retest={m_wr} "
                f"(n={len(with_retest)})"
            ),
        }
    )

    # H3 HTF conflict
    conf = subset(lambda r: r.get("htf_state") == "HTF_CONFLICT")
    aligned = subset(lambda r: r.get("htf_state") == "HTF_ALIGNED")
    m_c, m_a = _mean_r(conf), _mean_r(aligned)
    supported = None
    if len(conf) >= 30 and len(aligned) >= 30 and m_c is not None and m_a is not None:
        supported = m_c < m_a
    out.append(
        {
            "id": "H3",
            "statement": "HTF/local timeframe conflict causes poor entries.",
            "potential_issue": "HTF conflict",
            "n_conflict": len(conf),
            "n_aligned": len(aligned),
            "mean_R_conflict": m_c,
            "mean_R_aligned": m_a,
            "sample_status": sample_size_status(min(len(conf), len(aligned))),
            "status": _status(min(len(conf), len(aligned)), supported),
            "evidence": f"mean_R CONFLICT={m_c} (n={len(conf)}) vs ALIGNED={m_a} (n={len(aligned)})",
        }
    )

    # H4 sideways/chop
    chop = subset(lambda r: "CHOP" in ((r.get("regime") or {}).get("labels") or []) or (r.get("regime") or {}).get("primary") == "SIDEWAYS")
    trend = subset(
        lambda r: (r.get("regime") or {}).get("primary") in ("TRENDING_UP", "TRENDING_DOWN")
    )
    m_ch, m_tr = _mean_r(chop), _mean_r(trend)
    supported = None
    if len(chop) >= 30 and len(trend) >= 30 and m_ch is not None and m_tr is not None:
        supported = m_ch < m_tr
    out.append(
        {
            "id": "H4",
            "statement": "Strategy performs poorly in sideways/choppy conditions.",
            "potential_issue": "Sideways / chop regime",
            "n_chop_sideways": len(chop),
            "n_trending": len(trend),
            "mean_R_chop_sideways": m_ch,
            "mean_R_trending": m_tr,
            "sample_status": sample_size_status(min(len(chop), len(trend))),
            "status": _status(min(len(chop), len(trend)), supported),
            "evidence": f"mean_R chop/sideways={m_ch} (n={len(chop)}) vs trending={m_tr} (n={len(trend)})",
        }
    )

    # H5 entry TF too low vs HTF — compare 15m under conflict vs aligned
    t15_conf = subset(
        lambda r: str((r.get("trade") or {}).get("timeframe")).lower() == "15m"
        and r.get("htf_state") == "HTF_CONFLICT"
    )
    t15_al = subset(
        lambda r: str((r.get("trade") or {}).get("timeframe")).lower() == "15m"
        and r.get("htf_state") == "HTF_ALIGNED"
    )
    m15c, m15a = _mean_r(t15_conf), _mean_r(t15_al)
    supported = None
    if len(t15_conf) >= 30 and len(t15_al) >= 30 and m15c is not None and m15a is not None:
        supported = m15c < m15a
    out.append(
        {
            "id": "H5",
            "statement": "Entry timeframe is too low relative to HTF structure.",
            "potential_issue": "Wrong timeframe / HTF mismatch on lower TF",
            "n_15m_conflict": len(t15_conf),
            "n_15m_aligned": len(t15_al),
            "mean_R_15m_conflict": m15c,
            "mean_R_15m_aligned": m15a,
            "sample_status": sample_size_status(min(len(t15_conf), len(t15_al))),
            "status": _status(min(len(t15_conf), len(t15_al)), supported),
            "evidence": (
                f"15m mean_R CONFLICT={m15c} (n={len(t15_conf)}) vs ALIGNED={m15a} "
                f"(n={len(t15_al)})"
            ),
        }
    )

    # H6 vol expansion
    exp = subset(lambda r: "VOLATILITY_EXPANSION" in ((r.get("regime") or {}).get("labels") or []))
    other = subset(lambda r: "VOLATILITY_EXPANSION" not in ((r.get("regime") or {}).get("labels") or []))
    m_e, m_o = _mean_r(exp), _mean_r(other)
    supported = None
    if len(exp) >= 30 and len(other) >= 30 and m_e is not None and m_o is not None:
        supported = m_e < m_o
    out.append(
        {
            "id": "H6",
            "statement": "Trades occur after excessive volatility expansion.",
            "potential_issue": "Excessive volatility",
            "n_expansion": len(exp),
            "mean_R_expansion": m_e,
            "mean_R_other": m_o,
            "sample_status": sample_size_status(len(exp)),
            "status": _status(len(exp), supported),
            "evidence": f"mean_R VOL_EXPANSION={m_e} (n={len(exp)}) vs other={m_o}",
        }
    )

    # H7 vol contraction
    con = subset(lambda r: "VOLATILITY_CONTRACTION" in ((r.get("regime") or {}).get("labels") or []))
    m_con = _mean_r(con)
    supported = None
    if len(con) >= 30 and m_con is not None and overall.get("mean_R") is not None:
        supported = m_con < float(overall["mean_R"])
    out.append(
        {
            "id": "H7",
            "statement": "Trades occur during volatility compression.",
            "potential_issue": "Insufficient volatility",
            "n_contraction": len(con),
            "mean_R_contraction": m_con,
            "mean_R_overall": overall.get("mean_R"),
            "sample_status": sample_size_status(len(con)),
            "status": _status(len(con), supported),
            "evidence": f"mean_R VOL_CONTRACTION={m_con} (n={len(con)}) vs overall={overall.get('mean_R')}",
        }
    )

    # H8 sweep
    weak = subset(lambda r: (r.get("liquidity") or {}).get("state") in ("WEAK_SWEEP", "DEEP_SWEEP"))
    valid = subset(lambda r: (r.get("liquidity") or {}).get("state") == "VALID_SWEEP")
    m_w, m_v = _mean_r(weak), _mean_r(valid)
    supported = None
    if len(weak) >= 30 and len(valid) >= 30 and m_w is not None and m_v is not None:
        supported = m_w < m_v
    out.append(
        {
            "id": "H8",
            "statement": "Liquidity sweep is too weak/deep.",
            "potential_issue": "Weak/deep liquidity sweep",
            "n_weak_deep": len(weak),
            "n_valid": len(valid),
            "mean_R_weak_deep": m_w,
            "mean_R_valid": m_v,
            "sample_status": sample_size_status(min(len(weak), len(valid))),
            "status": _status(min(len(weak), len(valid)), supported),
            "evidence": f"mean_R weak/deep={m_w} (n={len(weak)}) vs valid={m_v} (n={len(valid)})",
        }
    )

    # H9 FVG/SD — FVG unavailable; use SD far
    far_sd = subset(lambda r: (r.get("supply_demand") or {}).get("state") == "FAR_FROM_ZONE")
    near_sd = subset(lambda r: (r.get("supply_demand") or {}).get("state") == "NEAR_ZONE")
    m_f, m_n = _mean_r(far_sd), _mean_r(near_sd)
    supported = None
    if len(far_sd) >= 30 and len(near_sd) >= 30 and m_f is not None and m_n is not None:
        supported = m_f < m_n
    out.append(
        {
            "id": "H9",
            "statement": "FVG/S&D location is poor.",
            "potential_issue": "Poor S/D location (FVG unavailable)",
            "n_far_sd": len(far_sd),
            "n_near_sd": len(near_sd),
            "mean_R_far": m_f,
            "mean_R_near": m_n,
            "fvg": "UNAVAILABLE",
            "sample_status": sample_size_status(min(len(far_sd), len(near_sd))),
            "status": _status(min(len(far_sd), len(near_sd)), supported),
            "evidence": (
                f"FVG=UNAVAILABLE; mean_R FAR_FROM_ZONE={m_f} (n={len(far_sd)}) vs "
                f"NEAR_ZONE={m_n} (n={len(near_sd)})"
            ),
        }
    )

    # H10 SL too tight
    tight = subset(
        lambda r: isinstance((r.get("stop_forensics") or {}).get("sl_distance_atr"), (int, float))
        and float((r.get("stop_forensics") or {}).get("sl_distance_atr")) <= SL_TIGHT_ATR_MAX
    )
    wider = subset(
        lambda r: isinstance((r.get("stop_forensics") or {}).get("sl_distance_atr"), (int, float))
        and float((r.get("stop_forensics") or {}).get("sl_distance_atr")) > SL_TIGHT_ATR_MAX
    )
    # Also: losses where MAE_R ~ 1 and never reached +0.5R
    stop_out_fast = subset(
        lambda r: _r(r) is not None
        and float(_r(r)) <= 0
        and (r.get("tp_forensics") or {}).get("reached_0_5R") is False
        and isinstance((r.get("stop_forensics") or {}).get("path", {}).get("mae_r"), (int, float))
        and float((r.get("stop_forensics") or {}).get("path", {}).get("mae_r")) >= 0.9
    )
    m_t, m_w2 = _mean_r(tight), _mean_r(wider)
    supported = None
    if len(tight) >= 30 and len(wider) >= 30 and m_t is not None and m_w2 is not None:
        supported = m_t < m_w2
    out.append(
        {
            "id": "H10",
            "statement": "SL is too tight relative to normal volatility.",
            "potential_issue": "SL too tight",
            "n_tight": len(tight),
            "n_wider": len(wider),
            "mean_R_tight": m_t,
            "mean_R_wider": m_w2,
            "n_loss_no_0_5R_mae_ge_0_9": len(stop_out_fast),
            "sample_status": sample_size_status(min(len(tight), len(wider))),
            "status": _status(min(len(tight), len(wider)), supported),
            "evidence": (
                f"mean_R SL<={SL_TIGHT_ATR_MAX}ATR={m_t} (n={len(tight)}) vs wider={m_w2} "
                f"(n={len(wider)}); losses with MAE_R>=0.9 and never +0.5R: {len(stop_out_fast)}"
            ),
        }
    )

    # H11 TP geometry — MFE often >> realized R on wins cut early / losses with MFE>1
    mfe_gt1_loss = subset(
        lambda r: _r(r) is not None
        and float(_r(r)) <= 0
        and isinstance((r.get("tp_forensics") or {}).get("mfe_r"), (int, float))
        and float((r.get("tp_forensics") or {}).get("mfe_r")) >= 1.0
    )
    supported = None
    if len(ok) >= 30:
        supported = len(mfe_gt1_loss) >= max(10, int(0.2 * sum(1 for r in ok if (_r(r) or 0) <= 0)))
    out.append(
        {
            "id": "H11",
            "statement": "TP geometry is inappropriate for the observed MAE/MFE.",
            "potential_issue": "TP problem",
            "n_losses_with_MFE_R_ge_1": len(mfe_gt1_loss),
            "sample_status": sample_size_status(len(ok)),
            "status": _status(len(ok), supported),
            "evidence": (
                f"Losing trades that still reached MFE_R>=1 before exit: "
                f"{len(mfe_gt1_loss)} (possible target/management geometry issue; descriptive only)"
            ),
        }
    )

    # H12 long/short asymmetry
    longs = subset(lambda r: (r.get("trade") or {}).get("direction") == "LONG")
    shorts = subset(lambda r: (r.get("trade") or {}).get("direction") == "SHORT")
    m_l, m_s = _mean_r(longs), _mean_r(shorts)
    supported = None
    if len(longs) >= 30 and len(shorts) >= 30 and m_l is not None and m_s is not None:
        supported = abs(m_l - m_s) >= 0.15
    out.append(
        {
            "id": "H12",
            "statement": "Long and short behavior is asymmetric.",
            "potential_issue": "Direction asymmetry",
            "n_long": len(longs),
            "n_short": len(shorts),
            "mean_R_long": m_l,
            "mean_R_short": m_s,
            "sample_status": sample_size_status(min(len(longs), len(shorts))),
            "status": _status(min(len(longs), len(shorts)), supported),
            "evidence": f"mean_R LONG={m_l} (n={len(longs)}) vs SHORT={m_s} (n={len(shorts)})",
        }
    )

    # H13 repeated trades same regime — concentration
    from collections import Counter

    keys = [
        f"{(r.get('trade') or {}).get('symbol')}|{(r.get('regime') or {}).get('primary')}|"
        f"{(r.get('session') or {}).get('bin')}"
        for r in ok
    ]
    cnt = Counter(keys)
    top = cnt.most_common(3)
    top_share = (top[0][1] / len(ok)) if ok and top else None
    supported = None
    if len(ok) >= 30 and top_share is not None:
        supported = top_share >= 0.25
    out.append(
        {
            "id": "H13",
            "statement": "Repeated trades occur during the same underlying market regime.",
            "potential_issue": "Repeated entries in same regime",
            "top_clusters": top,
            "top_share": top_share,
            "sample_status": sample_size_status(len(ok)),
            "status": _status(len(ok), supported),
            "evidence": f"Top symbol|regime|session cluster share={top_share}; top={top}",
        }
    )

    # H14 session differences
    by_sess: dict[str, list] = {}
    for r in ok:
        b = (r.get("session") or {}).get("bin") or "UNKNOWN"
        by_sess.setdefault(b, []).append(r)
    sess_means = {k: _mean_r(v) for k, v in by_sess.items() if len(v) >= 5}
    vals = [v for v in sess_means.values() if v is not None]
    supported = None
    if len(vals) >= 3 and len(ok) >= 30:
        supported = (max(vals) - min(vals)) >= 0.25
    out.append(
        {
            "id": "H14",
            "statement": "Certain sessions have materially different behavior.",
            "potential_issue": "Session / time-of-day effect",
            "session_mean_R": sess_means,
            "session_n": {k: len(v) for k, v in by_sess.items()},
            "sample_status": sample_size_status(len(ok)),
            "status": _status(len(ok), supported),
            "evidence": f"session mean_R={sess_means}; spread check vs 0.25R threshold",
        }
    )

    return out


def issue_table(hypotheses: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for h in hypotheses:
        rows.append(
            {
                "potential_issue": h.get("potential_issue") or h.get("statement"),
                "evidence": h.get("evidence"),
                "sample_size": h.get("sample_status"),
                "n_fields": {k: h.get(k) for k in h if k.startswith("n_")},
                "status": h.get("status"),
                "hypothesis_id": h.get("id"),
            }
        )
    return rows
