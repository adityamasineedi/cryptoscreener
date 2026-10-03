"""One-off COMBO_02 HTF fidelity audit: spot-check blotter vs engines + negatives."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar, htf_alignment_gate
from app.research.config import ResearchConfig
from app.research.service import _load_research_candles, _signal_config
from app.signals.bos_engine import detect_bos
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

OUT = Path(__file__).resolve().parent
BT_PATH = OUT / "combo02_audit_backtest.json"


def _norm_ts(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(ts).replace("+00:00", "Z")
    if "." in s and ("Z" in s or "+" in s[10:]):
        s = s.split(".")[0] + "Z"
    if len(s) >= 19 and "Z" not in s and "+" not in s[10:]:
        s = s[:19] + "Z"
    return s[:20] if s.endswith("Z") else s


def _candle_ts(c: dict) -> Any:
    return c.get("time") or c.get("open_time") or c.get("timestamp")


def _find_index(candles: list[dict], signal_time: str) -> int | None:
    target = _norm_ts(signal_time)
    for i, c in enumerate(candles):
        if _norm_ts(_candle_ts(c)) == target:
            return i
    t19 = target[:16]
    for i, c in enumerate(candles):
        if _norm_ts(_candle_ts(c))[:16] == t19:
            return i
    return None


def _win_rate(trades: list[dict]) -> float | None:
    closed = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
    if not closed:
        return None
    wins = sum(1 for t in closed if float(t.get("r_multiple") or 0) > 0)
    return wins / len(closed)


def _trade_count(res: dict) -> int:
    trades = res.get("trades") or []
    if not trades:
        return int(res.get("sample_size") or 0)
    n = 0
    for t in trades:
        outcome = t.outcome if hasattr(t, "outcome") else t.get("outcome")
        if outcome != "OPEN":
            n += 1
    return n


async def main() -> None:
    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings())
    if not db_manager.enabled or db_manager.engine is None:
        raise SystemExit(f"DB unavailable: status={db_manager.status}")
    cfg = _signal_config()
    rcfg = ResearchConfig()
    combo = get_combination("COMBO_02")
    local = get_combination("COMBO_02_LOCAL")
    bt = json.loads(BT_PATH.read_text(encoding="utf-8"))

    metrics = []
    for r in bt["rows"]:
        trades = r.get("trades") or []
        metrics.append(
            {
                "symbol": r["symbol"],
                "n": len([t for t in trades if t.get("outcome") != "OPEN"]),
                "win_rate": _win_rate(trades),
                "avg_R": r.get("average_R"),
                "pnl_usd_net": r.get("pnl_usd_net"),
                "max_drawdown_R": r.get("max_drawdown_R"),
                "period_start": r.get("period_start"),
                "period_end": r.get("period_end"),
                "bars_loaded": r.get("bars_loaded"),
            }
        )

    spot: list[dict[str, Any]] = []
    candle_cache: dict[tuple, list[dict]] = {}

    async def load(sym: str, tf: str, limit: int = 2500) -> list[dict]:
        key = (sym, tf, limit)
        if key not in candle_cache:
            candles, _, meta = await _load_research_candles(sym, tf, limit=limit)
            print(f"loaded {sym} {tf}: {len(candles)} source={meta.get('candle_source')}")
            candle_cache[key] = candles
        return candle_cache[key]

    for r in bt["rows"]:
        sym = r["symbol"]
        c1h = await load(sym, "1h", 2500)
        c4h = await load(sym, "4h", 800)
        for t in r.get("trades") or []:
            idx = t.get("entry_index")
            if idx is None or idx >= len(c1h):
                idx = _find_index(c1h, t["signal_time"])
            # Prefer signal_time match if entry_index drifts due to limit mismatch
            by_time = _find_index(c1h, t["signal_time"])
            if by_time is not None:
                idx = by_time
            if idx is None:
                spot.append(
                    {
                        "symbol": sym,
                        "signal_time": t["signal_time"],
                        "error": "bar_not_found",
                        "gate_pass": False,
                    }
                )
                print(f"FAIL {sym} {t['signal_time']} bar_not_found")
                continue

            swings = swings_for_timeframe(c1h, cfg, "1h", as_of_index=idx)
            trend_info = infer_trend(swings)
            trend = trend_info.get("trend")
            bos = detect_bos(
                c1h,
                swings,
                trend_info,
                symbol=sym,
                timeframe="1h",
                as_of_index=idx,
            )
            bos_ok = bool(
                bos
                and bos.get("state") == "CONFIRMED"
                and bos.get("direction") == "BULLISH_BOS"
            )
            htf_ok, htf_meta = htf_alignment_gate(
                symbol=sym,
                timeframe="1h",
                candles=c1h,
                as_of_index=idx,
                bos=bos or {"direction": "BULLISH_BOS", "state": "CONFIRMED"},
                signal_config=cfg,
                candles_1h=c1h,
                candles_4h=c4h,
                setup_trend_label=trend,
            )
            ev = evaluate_combination_at_bar(
                symbol=sym,
                timeframe="1h",
                candles=c1h,
                as_of_index=idx,
                combination=combo,
                signal_config=cfg,
                research_config=rcfg,
                compute_sd=False,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            snap = t.get("condition_snapshot") or {}
            fails: list[str] = []
            if trend != "BULLISH":
                fails.append(f"trend={trend}")
            if not bos_ok:
                fails.append(f"bos={bos.get('direction') if bos else None}/{bos.get('state') if bos else None}")
            if not htf_ok:
                fails.append(f"htf={htf_meta.get('htf_alignment')}")
            if snap.get("htf_alignment") != "HTF_ALIGNED":
                fails.append(f"snap_htf={snap.get('htf_alignment')}")
            if snap.get("trend_4h") != "BULLISH" or snap.get("trend_1h") != "BULLISH":
                fails.append(f"snap_trends={snap.get('trend_4h')}/{snap.get('trend_1h')}")
            if (ev or {}).get("status") != "LONG_ENTRY_CANDIDATE":
                fails.append(f"eval_status={(ev or {}).get('status')}")

            entry = float(t["entry_price"])
            stop = float(t["stop_price"])
            tp1 = float(t["tp1"]) if t.get("tp1") is not None else None
            outcome = str(t.get("outcome") or "")
            hit_sl = hit_tp = None
            for j in range(idx + 1, len(c1h)):
                lo = float(c1h[j]["low"])
                hi = float(c1h[j]["high"])
                if hit_sl is None and lo <= stop:
                    hit_sl = j
                if hit_tp is None and tp1 is not None and hi >= tp1:
                    hit_tp = j
                if hit_sl is not None or hit_tp is not None:
                    break

            path_ok = True
            path_note = ""
            exit_idx = t.get("exit_index")
            if "TP" in outcome.upper():
                if hit_tp is None:
                    path_ok = False
                    path_note = "TP outcome but TP1 not touched"
                elif hit_sl is not None and hit_sl < hit_tp:
                    path_ok = False
                    path_note = "SL touched before TP1"
                elif hit_sl is not None and hit_sl == hit_tp:
                    path_note = "same_bar_SL_and_TP1"
                elif exit_idx is not None and hit_tp != exit_idx:
                    path_note = f"tp_bar={hit_tp} blotter_exit_idx={exit_idx}"
            elif outcome.upper() in ("SL", "STOP", "STOP_LOSS") or outcome.upper().startswith("SL"):
                if hit_sl is None:
                    path_ok = False
                    path_note = "SL outcome but stop not touched"
                elif hit_tp is not None and hit_tp < hit_sl:
                    path_ok = False
                    path_note = "TP1 touched before SL"
                elif hit_sl is not None and hit_tp is not None and hit_sl == hit_tp:
                    path_note = "same_bar_SL_and_TP1"
                elif exit_idx is not None and hit_sl != exit_idx:
                    path_note = f"sl_bar={hit_sl} blotter_exit_idx={exit_idx}"

            # Entry price vs candle close (MARKET)
            entry_note = ""
            close_px = float(c1h[idx]["close"])
            if abs(close_px - entry) / max(entry, 1e-9) > 1e-6:
                # may be LIMIT_RETEST
                entry_note = f"entry≠close close={close_px}"

            row = {
                "symbol": sym,
                "signal_time": t["signal_time"],
                "outcome": outcome,
                "r_multiple": t.get("r_multiple"),
                "entry_index": idx,
                "exit_index": exit_idx,
                "recomputed_trend": trend,
                "recomputed_bos_ok": bos_ok,
                "recomputed_bos": {
                    "direction": (bos or {}).get("direction"),
                    "state": (bos or {}).get("state"),
                },
                "recomputed_htf_ok": htf_ok,
                "recomputed_htf_alignment": htf_meta.get("htf_alignment"),
                "recomputed_trend_4h": htf_meta.get("trend_4h"),
                "recomputed_trend_1h": htf_meta.get("trend_1h"),
                "snapshot": {
                    k: snap.get(k)
                    for k in (
                        "htf_alignment",
                        "trend_4h",
                        "trend_1h",
                        "bos",
                        "trend",
                        "htf",
                        "rr",
                        "entry_type",
                    )
                },
                "eval_status": (ev or {}).get("status"),
                "eval_entry": (ev or {}).get("entry_price"),
                "blotter_entry": entry,
                "blotter_stop": stop,
                "blotter_tp1": tp1,
                "blotter_exit": t.get("exit_price"),
                "gate_fails": fails,
                "gate_pass": not fails,
                "path_ok": path_ok,
                "path_note": path_note,
                "entry_note": entry_note,
            }
            spot.append(row)
            flag = "PASS" if not fails else "FAIL"
            print(
                f"{flag} {sym} {t['signal_time']} out={outcome} R={t.get('r_multiple')} "
                f"trend={trend} bos={bos_ok} htf={htf_meta.get('htf_alignment')} "
                f"4h={htf_meta.get('trend_4h')} fails={fails} path_ok={path_ok} {path_note}"
            )

    # Negatives / control on SOL
    sym = "SOLUSDT"
    c1h = await load(sym, "1h", 2500)
    c4h = await load(sym, "4h", 800)

    bt_combo = run_combination_backtest(
        sym,
        "1h",
        c1h,
        "COMBO_02",
        signal_config=cfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=c1h,
        candles_4h=c4h,
    )
    bt_local = run_combination_backtest(
        sym,
        "1h",
        c1h,
        "COMBO_02_LOCAL",
        signal_config=cfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=c1h,
        candles_4h=c4h,
    )
    bt_missing = run_combination_backtest(
        sym,
        "1h",
        c1h,
        "COMBO_02",
        signal_config=cfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=None,
        candles_4h=None,  # setup is 1h so 1h reuses; force fail by empty 4h
    )
    # Explicit empty 4h to avoid setup-TF reuse of 1h as 4h
    bt_missing_4h = run_combination_backtest(
        sym,
        "1h",
        c1h,
        "COMBO_02",
        signal_config=cfg,
        research_config=rcfg,
        direction_filter="LONG",
        candles_1h=c1h,
        candles_4h=[],
    )

    bearish_4h_longs_combo = 0
    bearish_4h_longs_local = 0
    bearish_windows_checked = 0
    for i in range(120, len(c1h) - 1, 4):
        _ok, meta = htf_alignment_gate(
            symbol=sym,
            timeframe="1h",
            candles=c1h,
            as_of_index=i,
            bos={"direction": "BULLISH_BOS", "state": "CONFIRMED"},
            signal_config=cfg,
            candles_1h=c1h,
            candles_4h=c4h,
        )
        if meta.get("trend_4h") != "BEARISH":
            continue
        bearish_windows_checked += 1
        ev_c = evaluate_combination_at_bar(
            symbol=sym,
            timeframe="1h",
            candles=c1h,
            as_of_index=i,
            combination=combo,
            signal_config=cfg,
            research_config=rcfg,
            compute_sd=False,
            candles_1h=c1h,
            candles_4h=c4h,
        )
        ev_l = evaluate_combination_at_bar(
            symbol=sym,
            timeframe="1h",
            candles=c1h,
            as_of_index=i,
            combination=local,
            signal_config=cfg,
            research_config=rcfg,
            compute_sd=False,
            candles_1h=c1h,
            candles_4h=c4h,
        )
        if (ev_c or {}).get("status") == "LONG_ENTRY_CANDIDATE":
            bearish_4h_longs_combo += 1
        if (ev_l or {}).get("status") == "LONG_ENTRY_CANDIDATE":
            bearish_4h_longs_local += 1

    negatives = {
        "SOLUSDT_COMBO_02_n": _trade_count(bt_combo),
        "SOLUSDT_COMBO_02_LOCAL_n": _trade_count(bt_local),
        "SOLUSDT_COMBO_02_missing_htf_args_n": _trade_count(bt_missing),
        "SOLUSDT_COMBO_02_empty_4h_n": _trade_count(bt_missing_4h),
        "bearish_4h_windows_sampled": bearish_windows_checked,
        "bearish_4h_LONG_setups_COMBO_02": bearish_4h_longs_combo,
        "bearish_4h_LONG_setups_COMBO_02_LOCAL": bearish_4h_longs_local,
        "local_stricter_than_combo": _trade_count(bt_local) >= _trade_count(bt_combo),
        "htf_reduces_trades": _trade_count(bt_local) > _trade_count(bt_combo),
    }

    report = {
        "playbook": bt.get("playbook"),
        "require_htf_alignment": bt.get("require_htf_alignment"),
        "combination_id": bt.get("combination_id"),
        "fee_model": bt.get("fee_model"),
        "risk_usd": bt.get("risk_usd"),
        "metrics": metrics,
        "spot_checks": spot,
        "spot_pass": sum(1 for s in spot if s.get("gate_pass")),
        "spot_fail": sum(1 for s in spot if s.get("gate_pass") is False),
        "path_fail": sum(1 for s in spot if s.get("path_ok") is False),
        "negatives": negatives,
    }
    out_path = OUT / "combo02_audit_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\n=== METRICS ===")
    print(json.dumps(metrics, indent=2))
    print("\n=== NEGATIVES ===")
    print(json.dumps(negatives, indent=2))
    print(
        f"\nspot_pass={report['spot_pass']} spot_fail={report['spot_fail']} "
        f"path_fail={report['path_fail']}"
    )
    print(f"Wrote {out_path}")
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
