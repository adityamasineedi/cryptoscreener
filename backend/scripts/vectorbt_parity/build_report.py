"""Merge the engine and COMBO parity runs into one table.

Reads backend/reports/vectorbt_parity/{engine,combo}_parity.json and writes
docs/vectorbt_parity_report.md. Counts only; no performance numbers.

Status per id:
  PASS   every compared cell has zero mismatches and at least one cell had trades
  FAIL   any cell has a mismatch or a VectorBT error
  EMPTY  every runnable cell produced zero closed trades (nothing was compared)
  SKIP   every cell was rejected by the engine or the screen
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPORTS = BACKEND / "reports" / "vectorbt_parity"
OUT = BACKEND.parent / "docs" / "vectorbt_parity_report.md"

COMBO_IDS = (
    "COMBO_01", "COMBO_02", "COMBO_02_LOCAL", "COMBO_02_CLOSED_HTF", "COMBO_02_V1",
    "COMBO_02_V2", "COMBO_02_V2_1A", "COMBO_02_V2_1_A", "COMBO_02_V2_1B", "COMBO_03",
    "COMBO_03_TRANSITION", "COMBO_03_TRANSITION_B", "COMBO_03_TRANSITION_C", "COMBO_04",
    "COMBO_04_HTF", "COMBO_05", "COMBO_06", "COMBO_07", "COMBO_08",
    "COMBO_SD_A", "COMBO_SD_B", "COMBO_SD_C", "COMBO_SD_C12", "COMBO_SD_C20",
    "COMBO_SD_C_FRESH", "COMBO_SD_D", "COMBO_SD_E", "COMBO_SD_F", "COMBO_SD_LIQ",
)
ENGINE_IDS = (
    "CANDLE12_V2", "CANDLE12_V3_EXEC_FIX", "LARGE_CAP_SWEEP_CHOCH", "MID_CAP_FVG_DISCOUNT",
    "SMALL_CAP_VOLUME_BOS", "OSE_A_corrected_baseline", "OSE_B_acceptance", "OSE_C_retest",
    "OSE_D_faster_confirmation",
)
NO_ENGINE = (
    "Trend pullback + zone + volume",
    "Sweep + CHoCH + volume",
    "BOS zone retest + volume",
    "Range sweep + volume",
    "Compression breakout + retest + volume",
    "CHoCH + supply/demand",
    "VWAP pullback",
    "Standalone BOS",
)


def _load(name: str) -> tuple[list[dict], dict]:
    path = REPORTS / name
    if not path.exists():
        return [], {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, {}
    cells = data.get("cells") or data.get("results") or []
    return list(cells), {k: v for k, v in data.items() if k not in ("cells", "results")}


def _id_status(cells: list[dict]) -> str:
    st = [str(c.get("status") or "").upper() for c in cells]
    if not st:
        return "MISSING"
    if "FAIL" in st:
        return "FAIL"
    if "PASS" in st:
        return "PASS"
    if "EMPTY" in st:
        return "EMPTY"
    return "SKIP"


def _cell(v) -> str:
    return "" if v is None else str(v)


def main() -> int:
    engine_cells, _ = _load("engine_parity.json")
    combo_cells, combo_meta = _load("combo_parity.json")
    by_id: dict[str, list[dict]] = {}
    for c in engine_cells + combo_cells:
        by_id.setdefault(str(c.get("strategy_id")), []).append(c)

    lines = [
        "# VectorBT parity check",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by "
        "`backend/scripts/vectorbt_parity/build_report.py`.",
        "",
        "Each strategy's own bar-loop engine decides every entry, stop, target and exit. "
        "VectorBT (`Portfolio.from_orders`, explicit prices, no built-in stops) re-executes "
        "those orders with the same fill price, fee rate and size. Compared per trade: entry "
        "index, entry price, stop, target, exit index, exit price, outcome, net R. Stop, target "
        "and outcome travel with each order as labels, so they can only differ if trades are "
        "paired wrongly; the independent checks are the bar indices, the fills, one open "
        "position at a time, and net R recomputed from VectorBT's fills and fees. "
        "Datasets: `fixture` = seeded synthetic 1h candles (scripts/vectorbt_parity/fixture.py); "
        "`real` = BTCUSDT 1h 2024-01-01 → 2024-06-30. Counts only; no performance figures.",
        "",
        "| Strategy id | Dataset | Direction | Bar-loop trades | VectorBT trades | Mismatches | First mismatched field | Cell |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    counts = {"PASS": 0, "FAIL": 0, "EMPTY": 0, "SKIP": 0, "MISSING": 0}
    id_rows = []
    for sid in ENGINE_IDS + COMBO_IDS:
        cells = sorted(
            by_id.get(sid, []),
            key=lambda c: (str(c.get("dataset")), str(c.get("direction"))),
        )
        status = _id_status(cells)
        counts[status] += 1
        id_rows.append((sid, status))
        if not cells:
            lines.append(f"| {sid} | — | — | | | | | MISSING (no run recorded) |")
            continue
        for c in cells:
            cs = str(c.get("status") or "").upper()
            note = cs
            if cs == "SKIP":
                note = f"SKIP: {c.get('skip_reason') or c.get('error') or ''}".strip()
            elif c.get("error"):
                note = f"{cs}: {c['error']}"
            lines.append(
                "| {sid} | {ds} | {dr} | {bl} | {vb} | {mm} | {ff} | {note} |".format(
                    sid=sid,
                    ds=_cell(c.get("dataset")),
                    dr=_cell(c.get("direction")),
                    bl=_cell(c.get("bar_loop_trades")) if cs != "SKIP" else "",
                    vb=_cell(c.get("vectorbt_trades")) if cs != "SKIP" else "",
                    mm=_cell(c.get("mismatches")) if cs != "SKIP" else "",
                    ff=_cell(c.get("first_mismatch_field")),
                    note=note.replace("|", "/"),
                )
            )
    lines += ["", "## Per-id verdict", "", "| Strategy id | Verdict |", "|---|---|"]
    lines += [f"| {sid} | {st} |" for sid, st in id_rows]
    lines += ["| " + name + " | NO ENGINE |" for name in NO_ENGINE]
    alias = combo_meta.get("v2_1a_alias_check")
    if alias is not None:
        lines += ["", "## COMBO_02_V2_1A vs COMBO_02_V2_1_A", "", "```", json.dumps(alias, indent=2), "```"]
    total = len(ENGINE_IDS) + len(COMBO_IDS)
    summary = (
        f"**Summary:** {total} ids with an engine — {counts['PASS']} passed, {counts['FAIL']} failed, "
        f"{counts['SKIP']} skipped, {counts['EMPTY']} had no trades to compare"
        + (f", {counts['MISSING']} not run" if counts["MISSING"] else "")
        + f". {len(NO_ENGINE)} strategies listed as NO ENGINE (not run)."
    )
    lines[2:2] = ["", summary]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
