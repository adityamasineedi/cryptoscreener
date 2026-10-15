"""Donchian Stage 0 (entry-filter subset approximation) and Donchian trail exit replay.

Read-only research script. Reads stored trade files, reads candles through a read-only
DB session (same pattern as scripts/strategy_rank/combo/run_combo_rank.py: asyncpg via
SQLAlchemy with default_transaction_read_only=on and a SELECT/SHOW-only statement guard).
Imports repo functions read-only (no patching):
  app.research.combination_backtest.resolve_intrabar_outcome
  app.research.bos_strategy_comparison.htf.build_htf_as_of_index_map_fully_closed
  app.research.ohlcv_structure_experiment.config (HOLD_BARS, SLIPPAGE_RATE)
and merge_rank.judge (REPORT pass rule).

Writes only under replay/out/donchian/<source>/.

Usage (from D:\\cryptoscreener\\backend):
  .venv\\Scripts\\python.exe -B scripts\\strategy_rank\\replay\\donchian_replay.py --source rank
  .venv\\Scripts\\python.exe -B scripts\\strategy_rank\\replay\\donchian_replay.py --source slip

--source rank : stored rank trade files (COMBO_* / LARGE_CAP optimistic; CANDLE12_V2 / OSE
                already carry 0.02% slippage in their stored fills).
--source slip : Agent 1 files replay/out/slip/<STRAT>__LONG__<SYM>__1h__IS.json for COMBO_02,
                COMBO_01, COMBO_02_LOCAL, LARGE_CAP_SWEEP_CHOCH; CANDLE12_V2 / OSE unchanged
                (their stored fills already are the 0.02% slippage model).
--source opt  : Agent 1 optimistic files replay/out/opt/... for the same four books (cross-check).
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import asyncio
import importlib.util
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(r"D:\cryptoscreener")
BACKEND = REPO / "backend"
SR = BACKEND / "scripts" / "strategy_rank"
HERE = SR / "replay"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
           "TRXUSDT", "AVAXUSDT", "LINKUSDT"]
COMBO_BOOKS = ["COMBO_02", "COMBO_01", "COMBO_02_LOCAL"]
MC_BOOKS = ["CANDLE12_V2", "LARGE_CAP_SWEEP_CHOCH", "OSE_A_corrected_baseline", "OSE_B_acceptance",
            "OSE_C_retest", "OSE_D_faster_confirmation"]
BOOKS = COMBO_BOOKS + MC_BOOKS
AGENT1_BOOKS = set(COMBO_BOOKS + ["LARGE_CAP_SWEEP_CHOCH"])
TAKER = 0.0004
SLIP = 0.0002
NS = (20, 55)
CH_TFS = ("1h", "4h")
TRAILS = [("1h", 20), ("1h", 55), ("4h", 20), ("4h", 55)]
H1 = timedelta(hours=1)
H4 = timedelta(hours=4)
LOAD_1H_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
LOAD_4H_FROM = datetime(2025, 11, 1, tzinfo=timezone.utc)
LOAD_TO = datetime(2026, 10, 14, tzinfo=timezone.utc)


# ----------------------------------------------------------------------------- DB (read-only)
def _dsn() -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
    raise RuntimeError("DATABASE_URL not found in .env")


SQL_AUDIT: dict[str, int] = {}
RO_PROOF: dict[str, str] = {}


async def _load_all() -> dict[tuple[str, str], list[dict[str, Any]]]:
    from sqlalchemy import event, text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        _dsn(), pool_size=1, max_overflow=0, pool_pre_ping=True,
        connect_args={"server_settings": {"default_transaction_read_only": "on",
                                          "application_name": "strategy_rank_donchian_readonly"}},
    )

    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        head = (statement or "").lstrip().split(None, 1)[0].upper() if (statement or "").strip() else "?"
        SQL_AUDIT[head] = SQL_AUDIT.get(head, 0) + 1
        if head not in ("SELECT", "SHOW"):
            raise RuntimeError(f"donchian_replay: non-SELECT statement blocked: {statement[:120]!r}")

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    try:
        async with engine.connect() as conn:
            a = (await conn.execute(text("SHOW default_transaction_read_only"))).scalar()
            b = (await conn.execute(text("SHOW transaction_read_only"))).scalar()
            RO_PROOF.update(default_transaction_read_only=str(a), transaction_read_only=str(b))
            if str(a) != "on" or str(b) != "on":
                raise RuntimeError(f"DB session is not read-only: {RO_PROOF}")
            for sym in SYMBOLS:
                for tf, lo in (("1h", LOAD_1H_FROM), ("4h", LOAD_4H_FROM)):
                    rows = (await conn.execute(text(
                        "SELECT time, open, high, low, close FROM ohlcv WHERE symbol = :s AND timeframe = :tf "
                        "AND time >= :a AND time < :b ORDER BY time ASC"),
                        {"s": sym, "tf": tf, "a": lo, "b": LOAD_TO})).all()
                    out[(sym, tf)] = [{
                        "time": r[0] if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc),
                        "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                    } for r in rows]
    finally:
        await engine.dispose()
    return out


# ----------------------------------------------------------------------------- helpers
def ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fnum(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def close_eq(a: float | None, b: float | None, rel: float = 1e-9) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def _load_judge():
    spec = importlib.util.spec_from_file_location("_merge_rank_ro", SR / "merge_rank.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.judge


JUDGE = _load_judge()


class Series:
    """1h candles + 4h candles for one symbol, with Donchian lookups."""

    def __init__(self, sym: str, c1: list[dict[str, Any]], c4: list[dict[str, Any]]) -> None:
        from app.research.bos_strategy_comparison.htf import build_htf_as_of_index_map_fully_closed

        self.sym = sym
        self.c1, self.c4 = c1, c4
        self.idx = {c["time"]: i for i, c in enumerate(c1)}
        self.h1 = [c["high"] for c in c1]
        self.l1 = [c["low"] for c in c1]
        self.cl = [c["close"] for c in c1]
        self.h4 = [c["high"] for c in c4]
        self.l4 = [c["low"] for c in c4]
        raw = build_htf_as_of_index_map_fully_closed(c1, c4, setup_timeframe="1h", htf_timeframe="4h")
        m: list[int | None] = []
        for i, j in enumerate(raw):
            # Repo map = last 4h bar closed by the 1h bar's CLOSE. Shift back one when that 4h
            # block contains the 1h bar itself, so only 4h bars closed before the 1h bar OPENS count.
            if j is not None and c4[j]["time"] <= c1[i]["time"] < c4[j]["time"] + H4:
                j = j - 1 if j >= 1 else None
            m.append(j)
        self.map4 = m
        # Continuity check (1h gaps break "prior N bars").
        self.gaps_1h = sum(1 for a, b in zip(c1, c1[1:]) if b["time"] - a["time"] != H1)
        self.gaps_4h = sum(1 for a, b in zip(c4, c4[1:]) if b["time"] - a["time"] != H4)

    def channel(self, i: int, tf: str, n: int) -> tuple[float, float] | None:
        """(upper, lower) from the prior n closed bars of tf, excluding 1h bar i."""
        if tf == "1h":
            if i - n < 0:
                return None
            return max(self.h1[i - n:i]), min(self.l1[i - n:i])
        j = self.map4[i]
        if j is None or j - n + 1 < 0:
            return None
        return max(self.h4[j - n + 1:j + 1]), min(self.l4[j - n + 1:j + 1])


# ----------------------------------------------------------------------------- trade loading
def load_book_trades(book: str, sym: str, source: str) -> tuple[list[dict[str, Any]], str, bool]:
    """Return (raw trades, path, slipped_fill_model)."""
    if source in ("slip", "opt") and book in AGENT1_BOOKS:
        p = HERE / "out" / source / f"{book}__LONG__{sym}__1h__IS.json"
        if not p.exists():
            raise FileNotFoundError(str(p))
        d = json.loads(p.read_text(encoding="utf-8"))
        return d["trades"], str(p), source == "slip"
    fam = "combo" if book in COMBO_BOOKS else "multicap"
    p = SR / fam / "trades" / f"{book}__LONG__{sym}__1h.json"
    return json.loads(p.read_text(encoding="utf-8")), str(p), book not in AGENT1_BOOKS


def normalize(book: str, sym: str, t: dict[str, Any], S: Series, slipped: bool, agent1: bool) -> dict[str, Any]:
    """Common record. Times are bar-open times; idx are indices into S.c1."""
    r: dict[str, Any] = {"book": book, "sym": sym, "flags": []}
    if book in COMBO_BOOKS:
        sig = ts(t["signal_time"])
        r["entry_bar"] = S.idx.get(sig)
        r["decision_bar"] = r["entry_bar"]
        r["entry_raw"] = float(t["entry"])
        r["stop"] = float(t["stop"])
        r["targets"] = [float(x) for x in (t.get("targets") or []) if x is not None]
        r["exit_level"] = float(t["exit_price"])
        r["outcome"] = t.get("outcome")
        r["mode"] = "combo"
    elif book == "LARGE_CAP_SWEEP_CHOCH":
        sig = ts(t.get("signal_time") or t.get("entry_time"))
        r["entry_bar"] = S.idx.get(sig)
        r["decision_bar"] = r["entry_bar"]
        r["entry_raw"] = float(t["entry_price"])
        r["stop"] = float(t["stop_price"])
        r["targets"] = [float(t[k]) for k in ("tp1", "tp2", "tp3") if t.get(k) is not None]
        r["exit_level"] = float(t["exit_price"])
        r["outcome"] = t.get("outcome")
        r["mode"] = "combo"
    elif book == "CANDLE12_V2":
        c2 = S.idx.get(ts(t["entry_time"]))
        r["entry_bar"] = c2
        r["decision_bar"] = (c2 - 1) if c2 is not None else None  # C1: its close decided the stop order
        r["entry_raw"] = float(t["entry_price"])
        r["stop"] = float(t["stop_price"])
        r["targets"] = [float(t[k]) for k in ("tp1", "tp2", "tp3") if t.get(k) is not None]
        r["exit_level"] = float(t.get("raw_exit_price") if t.get("raw_exit_price") is not None else t["exit_price"])
        r["outcome"] = t.get("outcome")
        r["mode"] = "candle12"
        if c2 is not None and c2 >= 1:
            # entry = C1.high * (1 + 0.0002) (+ tick 0) per candle12_v2._trigger_and_entry
            if not close_eq(S.h1[c2 - 1] * (1 + SLIP), r["entry_raw"], 1e-7):
                r["flags"].append("c1_trigger_mismatch")
    else:  # OSE
        e = S.idx.get(ts(t["entry_time"]))
        r["entry_bar"] = e
        r["entry_raw"] = float(t["entry_price"])
        r["stop"] = float(t["stop_price"])
        r["targets"] = [float(t["target_price"])]
        r["exit_level"] = float(t["exit_price"])
        r["outcome"] = t.get("outcome")
        r["mode"] = "ose"
        r["decision_bar"] = ose_break_bar(book, t, S, e, r["flags"])
        if e is not None and not close_eq(S.c1[e]["open"] * (1 + SLIP), r["entry_raw"], 1e-7):
            r["flags"].append("ose_entry_open_mismatch")
    r["exit_bar_stored"] = S.idx.get(ts(t["exit_time"]))
    # fills / economics
    if agent1:
        r["entry_fill"] = float(t["entry_fill"])
        r["exit_fill_stored"] = float(t["exit_fill"])
        r["gross"] = float(t["gross_pnl_usd"])
        r["fee"] = abs(float(t["fee_total_usd"]))
        r["net"] = float(t["net_pnl_usd"])
    elif book in COMBO_BOOKS:
        r["entry_fill"] = r["entry_raw"]
        r["exit_fill_stored"] = float(t["exit_price"])
        r["gross"] = float(t["gross"])
        r["fee"] = abs(float(t["fee"]))
        r["net"] = float(t["net"])
    else:
        r["entry_fill"] = float(t["entry_price"])
        r["exit_fill_stored"] = float(t["exit_price"])
        r["gross"] = float(t["gross_pnl_usd"])
        r["fee"] = abs(float(t["fee_total_usd"]))
        r["net"] = float(t["net_pnl_usd"])
    q = fnum(t.get("qty")) or fnum(t.get("quantity"))
    if q is None:
        q = r["fee"] / (TAKER * (r["entry_fill"] + r["exit_fill_stored"]))
    r["qty"] = q
    # fee/gross model parity (taker both sides, same qty)
    g2 = q * (r["exit_fill_stored"] - r["entry_fill"])
    f2 = TAKER * q * (r["entry_fill"] + r["exit_fill_stored"])
    r["econ_ok"] = abs(g2 - r["gross"]) < 1e-4 * max(1.0, abs(r["gross"])) + 1e-6 and abs(f2 - r["fee"]) < 1e-6 + 1e-6 * r["fee"]
    r["exit_slip"] = SLIP if slipped else 0.0
    r["signal_time"] = t.get("signal_time") or t.get("entry_time")
    r["exit_time"] = t.get("exit_time")
    return r


def ose_break_bar(book: str, t: dict[str, Any], S: Series, e: int | None, flags: list[str]) -> int | None:
    """Recover the break bar (first close through the pivot level) from the stored entry."""
    if e is None:
        return None
    try:
        level = float(str(t["event_id"]).rsplit(":", 1)[1])
    except Exception:  # noqa: BLE001
        flags.append("ose_no_level")
        return None
    cl, lo = S.cl, S.l1

    def cross(b: int) -> bool:
        return b >= 1 and cl[b - 1] <= level < cl[b]

    variant = book[4:]
    if variant in ("A_corrected_baseline", "D_faster_confirmation"):
        cands = [e - 1]
    elif variant == "B_acceptance":
        cands = [e - 2]
    else:
        cands = [e - 4, e - 3, e - 2]
    good = []
    for b in cands:
        if not cross(b):
            continue
        q = e - 1
        if variant == "B_acceptance" and not cl[q] > level:
            continue
        if variant == "C_retest":
            ok = True
            for k in range(b + 1, q):  # bars before qualification: neither invalidate nor qualify
                if cl[k] < level or (lo[k] <= level and cl[k] > level):
                    ok = False
                    break
            if not ok or not (lo[q] <= level and cl[q] > level) or q - b > 3:
                continue
        good.append(b)
    if len(good) != 1:
        flags.append(f"ose_break_unresolved({len(good)})")
        return good[-1] if good else None
    return good[0]


# ----------------------------------------------------------------------------- exit replay
def _bar(S: Series, i: int) -> tuple[float, float, float, float]:
    c = S.c1[i]
    return c["open"], c["high"], c["low"], c["close"]


def replay(r: dict[str, Any], S: Series, trail: tuple[str, int] | None) -> dict[str, Any] | None:
    """Return {exit_bar, exit_level, cause, fill} or None (no exit in data)."""
    from app.research.combination_backtest import resolve_intrabar_outcome
    from app.research.ohlcv_structure_experiment.config import HOLD_BARS, SLIPPAGE_RATE

    e = r["entry_bar"]
    stop, tg = r["stop"], r["targets"]
    n = len(S.c1)
    armed = False

    def trail_check(i: int) -> str | None:
        nonlocal armed
        if trail is None:
            return None
        ch = S.channel(i, trail[0], trail[1])
        if ch is None:
            return None
        up, lw = ch
        c = S.cl[i]
        fire = None
        if c < lw:
            fire = "trail-lower"
        elif armed and c < up:
            fire = "trail-inside"
        if c > up:
            armed = True
        return fire

    def out(i: int, level: float, cause: str, slipped_exit: bool) -> dict[str, Any]:
        fill = level * (1 - r["exit_slip"]) if slipped_exit else level
        return {"exit_bar": i, "exit_level": level, "cause": cause, "fill": fill}

    if r["mode"] == "combo":
        for i in range(e + 1, n):
            _o, h, l, c = _bar(S, i)
            oc, px, _amb = resolve_intrabar_outcome(direction="LONG", high=h, low=l, stop=stop,
                                                    targets=tg, handling="CONSERVATIVE")
            if oc is not None:
                return out(i, float(px), "stop" if oc == "SL" else "target", True)
            f = trail_check(i)
            if f:
                return out(i, c, f, True)
        return None
    if r["mode"] == "candle12":
        # Candle-2 (entry bar): intrabar resolution, gap-through + stop -> SL; ambiguous -> SL.
        o2, h2, l2, c2 = _bar(S, e)
        oc, px, amb = resolve_intrabar_outcome(direction="LONG", high=h2, low=l2, stop=stop, targets=tg,
                                               handling="CONSERVATIVE")
        if o2 >= r["entry_raw"] and l2 <= stop:
            oc, px = "SL", stop
        if oc is not None:
            return out(e, float(stop if oc == "SL" else px), "stop" if oc == "SL" else "target", True)
        f = trail_check(e)  # C2 close is after the intrabar stop-entry fill
        if f:
            return out(e, c2, f, True)
        for i in range(e + 1, n):
            _o, h, l, c = _bar(S, i)
            oc, px, amb = resolve_intrabar_outcome(direction="LONG", high=h, low=l, stop=stop, targets=tg,
                                                   handling="CONSERVATIVE")
            if oc is not None:
                return out(i, float(stop if oc == "SL" else px), "stop" if oc == "SL" else "target", True)
            f = trail_check(i)
            if f:
                return out(i, c, f, True)
        return None
    # OSE: entry at entry-bar open; bars entry..entry+HOLD_BARS-1; gap-through stop exits at the open
    # (unslipped, as the engine); same bar -> stop; stop/target unslipped; timeout close slipped.
    last = e + HOLD_BARS - 1
    if last >= n:
        return None
    target = tg[0]
    for i in range(e, last + 1):
        o, h, l, c = _bar(S, i)
        if i > e and o <= stop:
            return {"exit_bar": i, "exit_level": o, "cause": "stop", "fill": o}
        if l <= stop:
            return {"exit_bar": i, "exit_level": stop, "cause": "stop", "fill": stop}
        if h >= target:
            return {"exit_bar": i, "exit_level": target, "cause": "target", "fill": target}
        if i == last:
            px = c * (1 - SLIPPAGE_RATE)
            return {"exit_bar": i, "exit_level": px, "cause": "other", "fill": px}
        f = trail_check(i)
        if f:
            return {"exit_bar": i, "exit_level": c, "cause": f, "fill": c * (1 - SLIPPAGE_RATE)}
    return None


# ----------------------------------------------------------------------------- metrics
def metrics(trs: list[dict[str, Any]], book: str = "") -> dict[str, Any]:
    """trs: dicts with net, gross, fee, exit_time. Same formulas as the rank runners.
    Max DD order: combo runner = list (entry) order; multicap runner = sorted by exit time."""
    if book not in COMBO_BOOKS:
        trs = sorted(trs, key=lambda x: (str(x["exit_time"])))
    net = [x["net"] for x in trs]
    n = len(net)
    wins = sum(1 for v in net if v > 0)
    losses = sum(1 for v in net if v < 0)
    gw = sum(v for v in net if v > 0)
    gl = -sum(v for v in net if v < 0)
    eq = peak = worst = 0.0
    for v in net:
        eq += v
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    row = {"family": "replay", "timeframe": "1h", "trades_closed": n, "wins": wins, "losses": losses,
           "net_pnl_usd": sum(net), "gross_pnl_usd": sum(x["gross"] for x in trs),
           "fees_usd": -sum(x["fee"] for x in trs), "profit_factor": (gw / gl) if gl > 0 else None}
    ok, why = JUDGE(row)
    return {
        "trades": n, "win_pct": (100.0 * wins / n) if n else None, "net": row["net_pnl_usd"],
        "gross": row["gross_pnl_usd"], "fees": row["fees_usd"], "pf": row["profit_factor"],
        "max_dd": worst, "pass": ok, "reason": "; ".join(why),
        "net_ex_best": (row["net_pnl_usd"] - max(net)) if n else None,
    }


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=("rank", "slip", "opt"), default="rank")
    args = ap.parse_args()
    outdir = HERE / "out" / "donchian" / args.source
    outdir.mkdir(parents=True, exist_ok=True)

    if args.source in ("slip", "opt"):
        missing = [f"{b}__LONG__{s}__1h__IS.json" for b in AGENT1_BOOKS for s in SYMBOLS
                   if not (HERE / "out" / args.source / f"{b}__LONG__{s}__1h__IS.json").exists()]
        if missing:
            print(f"MISSING {len(missing)} Agent-1 files, e.g. {missing[:3]}")
            return 2

    candles = asyncio.run(_load_all())
    series = {s: Series(s, candles[(s, "1h")], candles[(s, "4h")]) for s in SYMBOLS}
    data_info = {s: {"1h": [str(series[s].c1[0]["time"]), str(series[s].c1[-1]["time"]), len(series[s].c1),
                            series[s].gaps_1h],
                     "4h": [str(series[s].c4[0]["time"]), str(series[s].c4[-1]["time"]), len(series[s].c4),
                            series[s].gaps_4h]} for s in SYMBOLS}

    recs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    files_meta: dict[str, Any] = {}
    for b in BOOKS:
        for s in SYMBOLS:
            raw, path, slipped = load_book_trades(b, s, args.source)
            agent1 = args.source in ("slip", "opt") and b in AGENT1_BOOKS
            files_meta[f"{b}|{s}"] = {"path": path, "slipped_fill_model": slipped, "n": len(raw)}
            recs[(b, s)] = [normalize(b, s, t, series[s], slipped, agent1) for t in raw]

    # ---------------- Stage 0
    stage0 = []
    filt_rows = []
    for b in BOOKS:
        for s in SYMBOLS:
            S = series[s]
            for r in recs[(b, s)]:
                d = r["decision_bar"]
                r["above"] = {}
                for n_ in NS:
                    for tf in CH_TFS:
                        ch = S.channel(d, tf, n_) if d is not None else None
                        r["above"][(tf, n_)] = None if ch is None else (S.cl[d] > (ch[0] + ch[1]) / 2.0)
            for n_ in NS:
                rs = recs[(b, s)]
                a1 = [r["above"][("1h", n_)] for r in rs]
                a4 = [r["above"][("4h", n_)] for r in rs]
                noch = sum(1 for x, y in zip(a1, a4) if x is None or y is None)
                stage0.append({
                    "book": b, "sym": s, "N": n_, "trades": len(rs),
                    "no_channel_1h": sum(1 for x in a1 if x is None),
                    "no_channel_4h": sum(1 for x in a4 if x is None),
                    "no_channel_any": noch,
                    "above_1h_mid": sum(1 for x in a1 if x is True),
                    "above_4h_mid": sum(1 for x in a4 if x is True),
                    "above_both": sum(1 for x, y in zip(a1, a4) if x is True and y is True),
                })
    base_pass: dict[str, set[str]] = {}
    base_cells: dict[tuple[str, str], dict[str, Any]] = {}
    for b in BOOKS:
        base_pass[b] = set()
        for s in SYMBOLS:
            m = metrics([{"net": r["net"], "gross": r["gross"], "fee": r["fee"], "exit_time": r["exit_time"]}
                         for r in recs[(b, s)]], b)
            base_cells[(b, s)] = m
            if m["pass"]:
                base_pass[b].add(s)
    for b in BOOKS:
        for n_ in NS:
            for fname in ("1h_mid", "4h_mid", "both"):
                removed = 0
                passes = set()
                cells = {}
                for s in SYMBOLS:
                    keep = []
                    for r in recs[(b, s)]:
                        x, y = r["above"][("1h", n_)], r["above"][("4h", n_)]
                        ok = (x is True) if fname == "1h_mid" else (y is True) if fname == "4h_mid" else (x is True and y is True)
                        if ok:
                            keep.append(r)
                        else:
                            removed += 1
                    m = metrics([{"net": r["net"], "gross": r["gross"], "fee": r["fee"], "exit_time": r["exit_time"]}
                                 for r in keep], b)
                    cells[s] = m
                    if m["pass"]:
                        passes.add(s)
                filt_rows.append({"book": b, "N": n_, "filter": fname, "removed": removed,
                                  "total": sum(len(recs[(b, s)]) for s in SYMBOLS),
                                  "pass_coins": sorted(passes), "base_pass_coins": sorted(base_pass[b]),
                                  "added": sorted(passes - base_pass[b]), "lost": sorted(base_pass[b] - passes),
                                  "cells": cells})

    # ---------------- exit replay
    parity = {}
    trail_cells = []
    cause_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_trade_out = []
    for b in BOOKS:
        pm = {"trades": 0, "match": 0, "mismatch": 0, "econ_mismatch": 0, "flags": defaultdict(int), "examples": []}
        for s in SYMBOLS:
            S = series[s]
            for r in recs[(b, s)]:
                pm["trades"] += 1
                for f in r["flags"]:
                    pm["flags"][f] += 1
                if not r["econ_ok"]:
                    pm["econ_mismatch"] += 1
                if r["entry_bar"] is None:
                    r["parity"] = False
                    pm["mismatch"] += 1
                    continue
                base = replay(r, S, None)
                ok = (base is not None and base["exit_bar"] == r["exit_bar_stored"]
                      and close_eq(base["exit_level"], r["exit_level"], 1e-9))
                r["parity"] = ok
                if ok:
                    pm["match"] += 1
                else:
                    pm["mismatch"] += 1
                    if len(pm["examples"]) < 5:
                        pm["examples"].append({"sym": s, "signal": r["signal_time"], "stored_exit": r["exit_time"],
                                               "stored_level": r["exit_level"], "stored_outcome": r["outcome"],
                                               "replay": None if base is None else {
                                                   "exit": str(S.c1[base["exit_bar"]]["time"]),
                                                   "level": base["exit_level"], "cause": base["cause"]}})
        pm["flags"] = dict(pm["flags"])
        pm["rate"] = pm["match"] / pm["trades"] if pm["trades"] else None
        parity[b] = pm

    for b in BOOKS:
        for s in SYMBOLS:
            S = series[s]
            matched = [r for r in recs[(b, s)] if r["parity"]]
            base_m = metrics([{"net": r["net"], "gross": r["gross"], "fee": r["fee"], "exit_time": r["exit_time"]}
                              for r in matched], b)
            base_m.update(book=b, sym=s, variant="base", excluded_mismatch=len(recs[(b, s)]) - len(matched))
            trail_cells.append(base_m)
            for tr in TRAILS:
                vname = f"trail_{tr[0]}_{tr[1]}"
                rows = []
                for r in matched:
                    res = replay(r, S, tr)
                    assert res is not None
                    cause_counts[f"{b}|{vname}"][res["cause"]] += 1
                    if res["exit_bar"] == r["exit_bar_stored"] and res["cause"] in ("stop", "target", "other"):
                        g, f, nt = r["gross"], r["fee"], r["net"]  # unchanged base exit: stored economics
                        xt = r["exit_time"]
                    else:
                        fill = res["fill"]
                        g = r["qty"] * (fill - r["entry_fill"])
                        f = TAKER * r["qty"] * (r["entry_fill"] + fill)
                        nt = g - f
                        xt = S.c1[res["exit_bar"]]["time"].isoformat()
                        per_trade_out.append({"book": b, "sym": s, "variant": vname, "signal": r["signal_time"],
                                              "stored_exit": r["exit_time"], "stored_net": r["net"],
                                              "new_exit": xt, "cause": res["cause"], "fill": fill, "net": nt})
                    rows.append({"net": nt, "gross": g, "fee": f, "exit_time": xt})
                m = metrics(rows, b)
                m.update(book=b, sym=s, variant=vname, excluded_mismatch=len(recs[(b, s)]) - len(matched))
                trail_cells.append(m)

    # base exit causes (stored)
    for b in BOOKS:
        for s in SYMBOLS:
            for r in recs[(b, s)]:
                if r["parity"]:
                    oc = str(r["outcome"]).upper()
                    cause_counts[f"{b}|base"]["stop" if oc in ("SL", "STOP") else "target" if oc.startswith("TP") or oc == "TARGET" else "other"] += 1

    summary_by_variant = defaultdict(dict)
    for c in trail_cells:
        k = (c["book"], c["variant"])
        d = summary_by_variant[k]
        d.setdefault("pass", []).append(c["sym"]) if c["pass"] else None
        d["net"] = d.get("net", 0.0) + (c["net"] or 0.0)
        d["fees"] = d.get("fees", 0.0) + (c["fees"] or 0.0)
        d["trades"] = d.get("trades", 0) + c["trades"]

    result = {
        "source": args.source, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "read_only_proof": RO_PROOF, "sql_audit": SQL_AUDIT, "data": data_info,
        "files": files_meta, "base_pass_coins": {b: sorted(v) for b, v in base_pass.items()},
        "base_cells": {f"{b}|{s}": m for (b, s), m in base_cells.items()},
        "stage0": stage0, "filters": filt_rows,
        "parity": parity,
        "trail_cells": trail_cells,
        "trail_summary": {f"{b}|{v}": {"pass": sorted(d.get("pass", [])), "net": d["net"], "fees": d["fees"],
                                       "trades": d["trades"]} for (b, v), d in summary_by_variant.items()},
        "exit_causes": {k: dict(v) for k, v in cause_counts.items()},
    }
    (outdir / "donchian_result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    (outdir / "trail_changed_trades.json").write_text(json.dumps(per_trade_out, indent=1, default=str),
                                                     encoding="utf-8")
    print(json.dumps({"ro": RO_PROOF, "sql": SQL_AUDIT,
                      "parity": {b: (p["match"], p["trades"], p["econ_mismatch"], p["flags"]) for b, p in parity.items()}},
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
