"""Diagnostic: timing proof for sample OOS trades using parquet cache. Read-only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(r"e:\cryptoscreener\backend")
TRADES = ROOT / "reports" / "combo02_v1_oos" / "oos_trades_20261005T054508Z.json"
CACHE = ROOT / "data" / "research_cache" / "ohlcv" / "ohlcv_v1"


def parse_ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def load_parquet(symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    # Prefer exact OOS window file, else long history
    patterns = [
        f"{symbol}_{tf}_{start}_{end}_ohlcv_v1.parquet",
        f"{symbol}_{tf}_2022-10-06_2026-10-05_ohlcv_v1.parquet",
        f"{symbol}_{tf}_2025-07-01_2026-09-30_ohlcv_v1.parquet",
    ]
    path = None
    for name in patterns:
        p = CACHE / name
        if p.exists():
            path = p
            break
    if path is None:
        # fuzzy
        matches = sorted(CACHE.glob(f"{symbol}_{tf}_*_ohlcv_v1.parquet"))
        if not matches:
            raise FileNotFoundError(f"no parquet for {symbol} {tf}")
        path = matches[-1]
    df = pd.read_parquet(path)
    # normalize time column
    for col in ("time", "timestamp", "open_time"):
        if col in df.columns:
            t = pd.to_datetime(df[col], utc=True)
            df = df.assign(_t=t).sort_values("_t").reset_index(drop=True)
            break
    else:
        raise KeyError(f"no time col in {path}")
    return df, path


def ohlc_row(df: pd.DataFrame, ts: datetime) -> dict | None:
    m = df[df["_t"] == pd.Timestamp(ts)]
    if m.empty:
        # try naive compare
        m = df[df["_t"].dt.floor("h") == pd.Timestamp(ts).floor("h")]
    if m.empty:
        return None
    r = m.iloc[0]
    return {
        "open": float(r["open"] if "open" in r else r.get("o")),
        "high": float(r["high"] if "high" in r else r.get("h")),
        "low": float(r["low"] if "low" in r else r.get("l")),
        "close": float(r["close"] if "close" in r else r.get("c")),
        "open_time": str(r["_t"]),
    }


def forming_4h_open(setup_open: datetime) -> datetime:
    # 4h bins: 0,4,8,12,16,20 UTC
    hour = setup_open.astimezone(timezone.utc).hour
    base = hour - (hour % 4)
    return setup_open.astimezone(timezone.utc).replace(
        hour=base, minute=0, second=0, microsecond=0
    )


def last_fully_closed_4h_open(setup_open: datetime) -> datetime:
    """4h that has closed by setup bar open time? Actually by setup bar CLOSE.
    Setup 1h open T means bar covers [T, T+1h). At decision=close, clock=T+1h.
    Last fully closed 4h has close_time <= T+1h, i.e. open <= T+1h-4h = T-3h.
    """
    decision = setup_open.astimezone(timezone.utc) + timedelta(hours=1)
    # largest 4h open with open+4h <= decision
    candidate = forming_4h_open(decision - timedelta(seconds=1))
    # if forming open + 4h > decision, step back
    while candidate + timedelta(hours=4) > decision:
        candidate -= timedelta(hours=4)
    return candidate


def main() -> None:
    trades = json.loads(TRADES.read_text(encoding="utf-8"))["trades"]
    # pick 5 diverse: BTC early, ETH concurrent loss day, SOL big winner, BTC DD trough, boundary
    picks = []
    want = [
        ("BTCUSDT", "2025-08-10T23:00:00+00:00"),
        ("ETHUSDT", "2026-03-05T"),  # concurrent loss cluster
        ("SOLUSDT", "2026-06-15T10:00:00+00:00"),
        ("BTCUSDT", "2026-05-05T"),  # DD trough area
        ("ETHUSDT", "2025-07-"),  # early OOS
    ]
    for sym, prefix in want:
        for t in trades:
            if t["symbol"] == sym and str(t.get("signal_time", "")).startswith(prefix):
                picks.append(t)
                break
    # ensure 5
    while len(picks) < 5:
        for t in trades:
            if t not in picks:
                picks.append(t)
                break

    proofs = []
    for t in picks[:5]:
        sym = t["symbol"]
        sig = parse_ts(t["signal_time"])
        df1, p1 = load_parquet(sym, "1h", "2025-07-01", "2026-09-30")
        df4, p4 = load_parquet(sym, "4h", "2025-07-01", "2026-09-30")
        bar1 = ohlc_row(df1, sig)
        form4_open = forming_4h_open(sig)
        closed4_open = last_fully_closed_4h_open(sig)
        bar4_engine = ohlc_row(df4, form4_open)
        bar4_strict = ohlc_row(df4, closed4_open)
        # engine join: largest 4h open <= setup open (candle_time = open)
        # equals forming_4h_open for 1h setup
        forming_close = form4_open + timedelta(hours=4)
        decision_ts = sig + timedelta(hours=1)  # closed-bar decision
        proofs.append(
            {
                "symbol": sym,
                "signal_time_1h_open": t["signal_time"],
                "decision_ts_inferred_bar_close": decision_ts.isoformat(),
                "1h_OHLC": bar1,
                "entry_price_trade": t["entry_price"],
                "entry_matches_1h_close": (
                    abs(float(t["entry_price"]) - float(bar1["close"])) < 1e-8
                    if bar1
                    else None
                ),
                "entry_type": t.get("entry_type"),
                "4h_used_by_engine_open": form4_open.isoformat(),
                "4h_used_OHLC": bar4_engine,
                "4h_engine_bar_close_time": forming_close.isoformat(),
                "4h_engine_bar_fully_closed_at_decision": forming_close
                <= decision_ts,
                "4h_last_fully_closed_open": closed4_open.isoformat(),
                "4h_strict_closed_OHLC": bar4_strict,
                "forming_vs_strict_same_bar": form4_open == closed4_open,
                "stop": t.get("stop_price"),
                "tp1": t.get("tp1"),
                "exit_time": t.get("exit_time"),
                "exit_reason": t.get("outcome"),
                "r_net": t.get("r_net"),
                "snapshot_htf": {
                    "htf_alignment": (t.get("condition_snapshot") or {}).get(
                        "htf_alignment"
                    ),
                    "trend_4h": (t.get("condition_snapshot") or {}).get("trend_4h"),
                    "trend_1h": (t.get("condition_snapshot") or {}).get("trend_1h"),
                },
                "parquet_1h": str(p1.name),
                "parquet_4h": str(p4.name),
            }
        )

    out = {
        "n_proofs": len(proofs),
        "forming_4h_mismatch_count": sum(
            1 for p in proofs if not p["forming_vs_strict_same_bar"]
        ),
        "entry_close_mismatch_count": sum(
            1 for p in proofs if p["entry_matches_1h_close"] is False
        ),
        "proofs": proofs,
        "interpretation": (
            "Engine joins 4h by open_time <= 1h open (htf.as_of_index_at_or_before). "
            "At 1h close decision, that 4h bar is fully closed ONLY if 1h open is the "
            "last hour of the 4h (hour%4==3). Otherwise engine uses a still-forming 4h."
        ),
    }
    out_path = ROOT / "reports" / "combo02_v1_oos" / "audit_timing_proof_latest.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
