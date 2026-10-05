"""Reconcile COMBO_02_V1 UI backtest dumps against trade ledgers.

Research-only. Does not change strategy rules or create paper/live trades.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "reports" / "combo02_v1_reconciliation_audit"
OUT = ROOT / "reconciliation_report.json"

FILES = {
    "e3e4c05faf4454ec_tail12200": ROOT / "tail_12200.json",
    "e3e4c05faf4454ec_tail8640": ROOT / "db_tail_e3e4c05f.json",
    "2e654bef298db484": ROOT / "ytd_2e654bef.json",
    "31b236ff74e99ba6": ROOT / "feb_apr_31b236ff.json",
}


def fnum(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def parse_ts(x: Any) -> datetime | None:
    if not x:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    s = str(x).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_open(t: dict[str, Any]) -> bool:
    st = str(t.get("status") or t.get("outcome") or "").upper()
    if st in {"OPEN", "PENDING"}:
        return True
    has_exit = (
        t.get("exit_time") is not None
        or t.get("exit_timestamp") is not None
        or t.get("exit_price") is not None
        or t.get("exit_index") is not None
    )
    if has_exit:
        return False
    if (
        t.get("r_multiple") is not None
        or t.get("r_gross") is not None
        or t.get("r") is not None
        or t.get("gross_pnl_usd") is not None
        or t.get("net_pnl_usd") is not None
    ):
        return False
    return bool(t.get("signal_time") or t.get("entry_time") or t.get("entry_timestamp"))


def trade_gross(t: dict[str, Any]) -> float | None:
    if t.get("gross_pnl_usd") is not None:
        return fnum(t["gross_pnl_usd"])
    if t.get("gross_pnl") is not None:
        return fnum(t["gross_pnl"])
    return None


def trade_net(t: dict[str, Any]) -> float | None:
    if t.get("net_pnl_usd") is not None:
        return fnum(t["net_pnl_usd"])
    if t.get("net_pnl") is not None:
        return fnum(t["net_pnl"])
    return fnum(t.get("pnl_usd_net"))


def trade_fee(t: dict[str, Any]) -> float | None:
    if t.get("fee_total_usd") is not None:
        return fnum(t["fee_total_usd"])
    if t.get("total_fee") is not None:
        return fnum(t["total_fee"])
    return fnum(t.get("fees_usd"))


def trade_r(t: dict[str, Any]) -> float | None:
    if t.get("r_multiple") is not None:
        return fnum(t["r_multiple"])
    if t.get("r_gross") is not None:
        return fnum(t["r_gross"])
    return fnum(t.get("r"))


def entry_ts(t: dict[str, Any]) -> Any:
    return t.get("signal_time") or t.get("entry_time") or t.get("entry_timestamp")


def exit_ts(t: dict[str, Any]) -> Any:
    return t.get("exit_time") or t.get("exit_timestamp")


def audit_file(label: str, path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    job_keys = [
        "job_id",
        "status",
        "configuration_fingerprint",
        "period_mode",
        "start_date",
        "end_date",
        "limit",
        "risk_usd",
        "principal_usd",
        "leverage",
        "taker_fee_pct",
        "maker_fee_pct",
        "direction",
        "combination_id",
        "strategy_id",
        "combo_version",
        "production_comparable",
        "mismatch_reasons",
        "requested_range",
        "htf_alignment",
        "htf_timeframes",
        "setup_timeframe",
        "risk_mode",
        "source",
        "safety_notice",
    ]
    out: dict[str, Any] = {
        "label": label,
        "path": str(path),
        "job": {k: data.get(k) for k in job_keys if k in data},
        "cells": [],
    }
    for row in rows:
        trades = list(row.get("trades") or [])
        open_tr = [t for t in trades if is_open(t)]
        closed_tr = [t for t in trades if not is_open(t)]

        gross = [trade_gross(t) for t in closed_tr]
        nets = [trade_net(t) for t in closed_tr]
        fees = [trade_fee(t) for t in closed_tr]
        rs = [trade_r(t) for t in closed_tr]
        rnets = [fnum(t.get("r_net")) for t in closed_tr]

        sum_gross = sum(g for g in gross if g is not None)
        sum_net = sum(n for n in nets if n is not None)
        sum_fees = sum(f for f in fees if f is not None)
        valid_r = [r for r in rs if r is not None]
        sum_r = sum(valid_r)
        avg_r = (sum_r / len(valid_r)) if valid_r else None
        valid_rn = [r for r in rnets if r is not None]
        avg_r_net = (sum(valid_rn) / len(valid_rn)) if valid_rn else None
        wins = [r for r in valid_r if r > 0]
        losses = [r for r in valid_r if r < 0]
        flats = [r for r in valid_r if r == 0]
        win_rate = (len(wins) / len(valid_r)) if valid_r else None
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = (gp / gl) if gl > 0 else None

        # dollar PF from net
        net_wins = [n for n in nets if n is not None and n > 0]
        net_losses = [n for n in nets if n is not None and n < 0]
        pf_usd = (sum(net_wins) / abs(sum(net_losses))) if net_losses else None

        principal = fnum(row.get("principal_usd") or data.get("principal_usd") or 1000) or 1000.0
        risk = fnum(row.get("risk_usd") or data.get("risk_usd") or 20) or 20.0
        ordered = sorted(
            closed_tr,
            key=lambda t: (
                parse_ts(exit_ts(t) or entry_ts(t)) or datetime.min,
                parse_ts(entry_ts(t)) or datetime.min,
                int(t.get("entry_index") or 0),
            ),
        )
        eq_usd = principal
        eq_r = 0.0
        peak_usd = eq_usd
        peak_r = 0.0
        max_dd_usd = 0.0
        # Metrics equity curve starts at 0.0 before first trade R.
        equity_r = [0.0]
        for t in ordered:
            n = trade_net(t)
            r = trade_r(t) or 0.0
            if n is None:
                g = trade_gross(t)
                n = g if g is not None else r * risk
            eq_usd += n
            eq_r += r
            equity_r.append(eq_r)
            peak_usd = max(peak_usd, eq_usd)
            max_dd_usd = max(max_dd_usd, peak_usd - eq_usd)
        # Match metrics.max_drawdown_r (peak-to-trough on cumulative R incl. leading 0)
        peak_r = equity_r[0]
        max_dd_r = 0.0
        for e in equity_r:
            peak_r = max(peak_r, e)
            max_dd_r = max(max_dd_r, peak_r - e)

        ids = [t.get("trade_id") or t.get("id") or t.get("uid") for t in trades]
        entries = [str(entry_ts(t)) for t in trades]
        exits = [str(exit_ts(t)) for t in trades if exit_ts(t)]
        id_dup = [k for k, v in Counter([i for i in ids if i]).items() if v > 1]
        entry_dup = [k for k, v in Counter(entries).items() if v > 1]
        exit_dup = [k for k, v in Counter(exits).items() if v > 1]
        # also entry_index uniqueness if present
        entry_idx = [t.get("entry_index") for t in trades if t.get("entry_index") is not None]
        entry_idx_dup = [k for k, v in Counter(entry_idx).items() if v > 1]

        reported = {
            "sample_size": row.get("sample_size"),
            "pnl_usd": row.get("pnl_usd"),
            "pnl_usd_net": row.get("pnl_usd_net"),
            "fees_usd": row.get("fees_usd"),
            "average_R": row.get("average_R"),
            "average_R_net": row.get("average_R_net"),
            "expectancy_R": row.get("expectancy_R"),
            "profit_factor": row.get("profit_factor"),
            "max_drawdown_R": row.get("max_drawdown_R"),
            "tp1_hit_rate": row.get("tp1_hit_rate"),
            "sl_rate": row.get("sl_rate"),
            "tp1_hits": row.get("tp1_hits"),
            "sl_hits": row.get("sl_hits"),
            "bars_loaded": row.get("bars_loaded"),
            "candle_source": row.get("candle_source"),
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "requested_range": row.get("requested_range"),
            "actual_range": row.get("actual_range"),
            "approx_duration_days": row.get("approx_duration_days"),
            "configuration_fingerprint": row.get("configuration_fingerprint")
            or data.get("configuration_fingerprint"),
            "production_comparable": row.get("production_comparable"),
            "htf_alignment": row.get("htf_alignment") or data.get("htf_alignment"),
            "strategy_id": row.get("strategy_id") or data.get("strategy_id"),
            "combo_version": row.get("combo_version") or data.get("combo_version"),
            "risk_source": row.get("risk_source"),
            "effective_risk_amount": row.get("effective_risk_amount"),
            "effective_risk_percent": row.get("effective_risk_percent"),
            "leverage": row.get("leverage"),
            "taker_fee_pct": (row.get("fee_metadata") or data.get("fee_metadata") or {}).get(
                "taker_fee_pct"
            ),
            "maker_fee_pct": (row.get("fee_metadata") or data.get("fee_metadata") or {}).get(
                "maker_fee_pct"
            ),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "direction": row.get("direction"),
            "setup_timeframe": row.get("setup_timeframe") or data.get("setup_timeframe"),
            "htf_timeframes": row.get("htf_timeframes") or data.get("htf_timeframes"),
            "source": row.get("source") or data.get("source"),
            "r_values_len": len(row.get("r_values") or []),
            "equity_curve_r_len": len(row.get("equity_curve_r") or []),
        }

        # Range handling diagnosis
        req = reported.get("requested_range") or data.get("requested_range") or {}
        mode = (req.get("mode") if isinstance(req, dict) else None) or data.get("period_mode")
        start = data.get("start_date") or (req.get("start_date") if isinstance(req, dict) else None)
        end = data.get("end_date") or (req.get("end_date") if isinstance(req, dict) else None)
        limit = data.get("limit") or (req.get("limit") if isinstance(req, dict) else None)
        silent_tail = bool(
            (mode in {"CALENDAR_RANGE", "dates"} or start or end)
            and not start
            and not end
        )
        # calendar requested but actual looks like long tail before start?
        calendar_to_tail = False
        if start and reported.get("period_start"):
            ps = parse_ts(reported["period_start"])
            rs = parse_ts(start)
            if ps and rs and ps < rs:
                # warmup expected; only flag if mode claims calendar but period starts way before without warmup note
                calendar_to_tail = False  # warmup is OK
        if mode == "DB_TAIL" or (not start and not end):
            range_kind = "DB_TAIL"
        elif start or end:
            range_kind = "CALENDAR_RANGE"
        else:
            range_kind = str(mode)

        sample_keys = sorted(closed_tr[0].keys()) if closed_tr else []
        issues: list[dict[str, Any]] = []
        if abs((fnum(reported["pnl_usd"]) or 0) - sum_gross) > 1e-4:
            issues.append(
                {
                    "severity": "HIGH",
                    "code": "gross_pnl_mismatch",
                    "detail": f"report pnl_usd={reported['pnl_usd']} ledger_gross={sum_gross}",
                }
            )
        if abs((fnum(reported["pnl_usd_net"]) or 0) - sum_net) > 1e-4:
            issues.append(
                {
                    "severity": "HIGH",
                    "code": "net_pnl_mismatch",
                    "detail": f"report pnl_usd_net={reported['pnl_usd_net']} ledger_net={sum_net}",
                }
            )
        if reported["sample_size"] != len(closed_tr):
            issues.append(
                {
                    "severity": "HIGH" if open_tr else "MEDIUM",
                    "code": "sample_size_vs_closed",
                    "detail": f"sample_size={reported['sample_size']} closed={len(closed_tr)} open={len(open_tr)} trades={len(trades)}",
                }
            )
        if id_dup:
            issues.append({"severity": "HIGH", "code": "duplicate_trade_ids", "detail": id_dup})
        if entry_dup:
            issues.append({"severity": "MEDIUM", "code": "duplicate_entry_timestamps", "detail": entry_dup})
        if exit_dup:
            issues.append({"severity": "LOW", "code": "duplicate_exit_timestamps", "detail": exit_dup})
        if entry_idx_dup:
            issues.append({"severity": "HIGH", "code": "duplicate_entry_index", "detail": entry_idx_dup})
        if reported["average_R"] is not None and avg_r is not None:
            if abs(float(reported["average_R"]) - avg_r) > 1e-6:
                issues.append(
                    {
                        "severity": "HIGH",
                        "code": "average_R_mismatch",
                        "detail": f"report={reported['average_R']} ledger={avg_r}",
                    }
                )
        if reported["max_drawdown_R"] is not None:
            if abs(float(reported["max_drawdown_R"]) - max_dd_r) > 0.05:
                issues.append(
                    {
                        "severity": "MEDIUM",
                        "code": "max_dd_R_mismatch",
                        "detail": f"report={reported['max_drawdown_R']} ledger={max_dd_r}",
                    }
                )
        if silent_tail:
            issues.append(
                {
                    "severity": "HIGH",
                    "code": "calendar_silently_became_tail",
                    "detail": "calendar mode without start/end",
                }
            )

        # UI confusion: +274 vs +231
        ui_confusion = {
            "report_gross_pnl_usd": reported["pnl_usd"],
            "report_net_pnl_usd": reported["pnl_usd_net"],
            "ledger_gross": sum_gross,
            "ledger_net": sum_net,
            "ledger_fees": sum_fees,
            "gross_plus_fees_equals_net": abs(sum_gross + sum_fees - sum_net) < 1e-4
            if sum_fees <= 0
            else abs(sum_gross - abs(sum_fees) - sum_net) < 1e-4,
            "explanation": (
                "pnl_usd is GROSS (before fees). pnl_usd_net is AFTER fees. "
                "Visible ~+$231.28 matches net; report ~+$274.04 matches gross."
                if abs(sum_gross - 274.04) < 0.5 and abs(sum_net - 231.28) < 0.5
                else "Compare report pnl_usd (gross) vs pnl_usd_net (after fees)."
            ),
        }

        out["cells"].append(
            {
                "trade_count": len(trades),
                "closed_count": len(closed_tr),
                "open_count": len(open_tr),
                "sample_keys": sample_keys,
                "recomputed": {
                    "sum_gross_pnl_usd": sum_gross,
                    "sum_net_pnl_usd": sum_net,
                    "sum_fees_usd": sum_fees,
                    "sum_R": sum_r,
                    "average_R": avg_r,
                    "average_R_net": avg_r_net,
                    "win_rate": win_rate,
                    "wins": len(wins),
                    "losses": len(losses),
                    "flats": len(flats),
                    "profit_factor_R": pf,
                    "profit_factor_usd_net": pf_usd,
                    "ending_balance_usd": eq_usd,
                    "max_drawdown_usd": max_dd_usd,
                    "max_drawdown_R": max_dd_r,
                    "principal_usd": principal,
                    "risk_usd": risk,
                },
                "reported": reported,
                "deltas": {
                    "gross": (fnum(reported["pnl_usd"]) or 0) - sum_gross,
                    "net": (fnum(reported["pnl_usd_net"]) or 0) - sum_net,
                    "fees": (fnum(reported["fees_usd"]) or 0) - sum_fees,
                    "average_R": None
                    if reported["average_R"] is None or avg_r is None
                    else fnum(reported["average_R"]) - avg_r,
                    "max_dd_R": None
                    if reported["max_drawdown_R"] is None
                    else fnum(reported["max_drawdown_R"]) - max_dd_r,
                },
                "uniqueness": {
                    "id_dups": id_dup,
                    "entry_dups": entry_dup,
                    "exit_dups": exit_dup,
                    "entry_index_dups": entry_idx_dup,
                    "ids_present": sum(1 for i in ids if i),
                    "ids_missing": sum(1 for i in ids if not i),
                },
                "range": {
                    "kind": range_kind,
                    "mode": mode,
                    "start_date": start,
                    "end_date": end,
                    "limit": limit,
                    "period_start": reported["period_start"],
                    "period_end": reported["period_end"],
                    "actual_range": reported["actual_range"],
                    "silent_calendar_to_tail": silent_tail or calendar_to_tail,
                    "bars_loaded": reported["bars_loaded"],
                    "candle_source": reported["candle_source"],
                },
                "ui_pnl_confusion": ui_confusion,
                "open_trade_in_summary": {
                    "open_count": len(open_tr),
                    "sample_size": reported["sample_size"],
                    "sample_equals_closed": reported["sample_size"] == len(closed_tr),
                    "sample_equals_all_trades": reported["sample_size"] == len(trades),
                },
                "issues": issues,
            }
        )
    return out


CONFIG_COMPARE_FIELDS = [
    "configuration_fingerprint",
    "strategy_id",
    "combo_version",
    "source",
    "direction",
    "setup_timeframe",
    "htf_timeframes",
    "htf_alignment",
    "risk_source",
    "effective_risk_amount",
    "effective_risk_percent",
    "leverage",
    "taker_fee_pct",
    "maker_fee_pct",
    "production_comparable",
    "symbol",
    "timeframe",
]


def config_diff(audits: list[dict[str, Any]]) -> dict[str, Any]:
    configs = {}
    for a in audits:
        if not a["cells"]:
            continue
        cell = a["cells"][0]
        fp = cell["reported"].get("configuration_fingerprint") or a["label"]
        # Prefer the three target fingerprints; include limit in key for e3e4 duplicates
        key = f"{fp}::{a['label']}"
        cfg = {f: cell["reported"].get(f) for f in CONFIG_COMPARE_FIELDS}
        cfg.update(
            {
                "period_mode": a["job"].get("period_mode") or cell["range"].get("mode"),
                "start_date": a["job"].get("start_date") or cell["range"].get("start_date"),
                "end_date": a["job"].get("end_date") or cell["range"].get("end_date"),
                "limit": a["job"].get("limit") or cell["range"].get("limit"),
                "candle_source": cell["range"].get("candle_source"),
                "bars_loaded": cell["range"].get("bars_loaded"),
                "period_start": cell["reported"].get("period_start"),
                "period_end": cell["reported"].get("period_end"),
            }
        )
        configs[key] = cfg

    # field-by-field across all
    all_fields = sorted({k for c in configs.values() for k in c})
    diffs = {}
    for field in all_fields:
        vals = {name: cfg.get(field) for name, cfg in configs.items()}
        uniq = {json.dumps(v, sort_keys=True, default=str) for v in vals.values()}
        if len(uniq) > 1:
            diffs[field] = vals
    return {"configs": configs, "differing_fields": diffs}


def main() -> None:
    audits = [audit_file(k, p) for k, p in FILES.items() if p.exists()]
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "disclaimer": (
            "Historical research reconciliation only. Not a profitability claim. "
            "No strategy parameters changed. No paper/live trades created."
        ),
        "audits": audits,
        "configuration_diff": config_diff(audits),
        "headline_33_trade_pnl": None,
    }
    # Find 33-trade run
    for a in audits:
        for c in a["cells"]:
            if c["closed_count"] == 33 or c["trade_count"] == 33:
                report["headline_33_trade_pnl"] = {
                    "label": a["label"],
                    "path": a["path"],
                    "fingerprint": c["reported"].get("configuration_fingerprint"),
                    "ui_confusion": c["ui_pnl_confusion"],
                    "recomputed": c["recomputed"],
                    "reported": {
                        "pnl_usd": c["reported"]["pnl_usd"],
                        "pnl_usd_net": c["reported"]["pnl_usd_net"],
                        "fees_usd": c["reported"]["fees_usd"],
                        "sample_size": c["reported"]["sample_size"],
                    },
                    "issues": c["issues"],
                }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT}")
    # compact console summary
    for a in audits:
        print("\n===", a["label"], "===")
        print("job:", {k: a["job"].get(k) for k in ["configuration_fingerprint", "period_mode", "start_date", "end_date", "limit"]})
        for c in a["cells"]:
            print(
                "trades",
                c["trade_count"],
                "closed",
                c["closed_count"],
                "open",
                c["open_count"],
            )
            print(
                "gross",
                round(c["recomputed"]["sum_gross_pnl_usd"], 4),
                "net",
                round(c["recomputed"]["sum_net_pnl_usd"], 4),
                "fees",
                round(c["recomputed"]["sum_fees_usd"], 4),
            )
            print(
                "report gross/net/fees",
                c["reported"]["pnl_usd"],
                c["reported"]["pnl_usd_net"],
                c["reported"]["fees_usd"],
            )
            print("avgR", c["recomputed"]["average_R"], "report", c["reported"]["average_R"])
            print("PF_R", c["recomputed"]["profit_factor_R"], "report", c["reported"]["profit_factor"])
            print("maxDD_R", c["recomputed"]["max_drawdown_R"], "report", c["reported"]["max_drawdown_R"])
            print("endBal", round(c["recomputed"]["ending_balance_usd"], 4))
            print("range", c["range"]["kind"], c["range"]["period_start"], "->", c["range"]["period_end"])
            print("issues", c["issues"])
            print("uniqueness", c["uniqueness"])
    print("\nDiffering config fields:", list(report["configuration_diff"]["differing_fields"].keys()))
    if report["headline_33_trade_pnl"]:
        print("\nHEADLINE:", json.dumps(report["headline_33_trade_pnl"]["ui_confusion"], indent=2))


if __name__ == "__main__":
    main()
