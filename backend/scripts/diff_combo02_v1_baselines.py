"""Diff two COMBO_02 v1 regression baseline JSON files (before vs after)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _trade_key(t: dict[str, Any]) -> str:
    return "|".join(
        [
            str(t.get("symbol") or ""),
            str(t.get("timeframe") or ""),
            str(t.get("signal_time") or ""),
            str(t.get("entry_price") or ""),
            str(t.get("stop_price") or ""),
            str(t.get("tp1") or ""),
            str(t.get("outcome") or ""),
        ]
    )


def _trades_by_symbol(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("rows") or []:
        sym = str(row.get("symbol") or "")
        closed = [
            t
            for t in (row.get("trades") or [])
            if t.get("outcome") not in (None, "OPEN")
        ]
        out[sym] = closed
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b_trades = _trades_by_symbol(before)
    a_trades = _trades_by_symbol(after)
    symbols = sorted(set(b_trades) | set(a_trades))
    per_symbol: list[dict[str, Any]] = []
    total_added = 0
    total_removed = 0
    total_changed = 0

    for sym in symbols:
        b_map = {_trade_key(t): t for t in b_trades.get(sym, [])}
        a_map = {_trade_key(t): t for t in a_trades.get(sym, [])}
        added_keys = sorted(set(a_map) - set(b_map))
        removed_keys = sorted(set(b_map) - set(a_map))
        # Same signal_time but different prices/outcomes
        b_by_sig = {str(t.get("signal_time")): t for t in b_trades.get(sym, [])}
        a_by_sig = {str(t.get("signal_time")): t for t in a_trades.get(sym, [])}
        changed: list[dict[str, Any]] = []
        for sig in sorted(set(b_by_sig) & set(a_by_sig)):
            bt, at = b_by_sig[sig], a_by_sig[sig]
            if _trade_key(bt) == _trade_key(at):
                # Check pnl/r drift on identical key
                drifts = {}
                for field in ("r_multiple", "r_net", "net_pnl_usd", "exit_price"):
                    bv, av = bt.get(field), at.get(field)
                    if bv != av:
                        drifts[field] = {"before": bv, "after": av}
                if drifts:
                    changed.append({"signal_time": sig, "drifts": drifts})
            else:
                changed.append(
                    {
                        "signal_time": sig,
                        "before_key": _trade_key(bt),
                        "after_key": _trade_key(at),
                    }
                )

        total_added += len(added_keys)
        total_removed += len(removed_keys)
        total_changed += len(changed)
        b_sum = next(
            (s for s in (before.get("summaries") or []) if s.get("symbol") == sym),
            {},
        )
        a_sum = next(
            (s for s in (after.get("summaries") or []) if s.get("symbol") == sym),
            {},
        )
        per_symbol.append(
            {
                "symbol": sym,
                "before_n": len(b_map),
                "after_n": len(a_map),
                "added": added_keys,
                "removed": removed_keys,
                "changed": changed,
                "before_summary": b_sum,
                "after_summary": a_sum,
            }
        )

    if total_added > 0:
        verdict = "MATERIALLY_CHANGED_MORE_TRADES"
    elif total_removed > 0 or total_changed > 0:
        verdict = "SAFELY_NARROWED_OR_CHANGED"
        if total_removed > 0 and total_changed == 0 and total_added == 0:
            # Check if remaining trades are exact subset
            verdict = "SAFELY_NARROWED"
    else:
        verdict = "UNCHANGED"

    return {
        "verdict": verdict,
        "param_fingerprint_before": before.get("param_fingerprint"),
        "param_fingerprint_after": after.get("param_fingerprint"),
        "total_added": total_added,
        "total_removed": total_removed,
        "total_changed": total_changed,
        "per_symbol": per_symbol,
        "assert_hints": [
            "Trade count must not increase unless justified by a bug fix",
            "No trade without HTF_ALIGNED",
            "No trade after HL_BROKEN / CHOCH_BEARISH",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", required=True, type=Path)
    p.add_argument("--after", required=True, type=Path)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    report = diff(before, after)
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
