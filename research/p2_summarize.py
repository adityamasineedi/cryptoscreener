"""Builds the Phase 2 look-ahead table from research/out/p2/lookahead_<coin>.json and inserts it into REPORT.md
(replacing LOOKAHEAD_TABLE_PLACEHOLDER, or the table between the markers on re-runs).

Run from repo root:  backend/.venv/Scripts/python.exe research/p2_summarize.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
P2 = ROOT / "research" / "out" / "p2"
REPORT = ROOT / "research" / "REPORT.md"
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BEGIN, END = "<!-- lookahead:begin -->", "<!-- lookahead:end -->"


def main() -> None:
    res = {c: json.loads((P2 / f"lookahead_{c}.json").read_text(encoding="utf-8")) for c in COINS}
    rows = []
    for tf in ("1h", "4h", "1d", "1h<-4h"):
        for name in res[COINS[0]]["detectors"][tf]:
            cells = [res[c]["detectors"][tf][name] for c in COINS]
            rows.append((f"detector `{name}` ({tf})", cells, "tested", name.startswith("CONTROL")))
    for name in res[COINS[0]]["setups"]:
        cells = [res[c]["setups"][name] for c in COINS]
        rows.append((f"setups `{name}`", cells, "tested", name.startswith("CONTROL")))
    lines = ["| Item | " + " | ".join(f"{c[:3]} truncations / mismatches" for c in COINS) + " | Result |",
             "|---|" + "---|" * (len(COINS) + 1)]
    all_ok = True
    for label, cells, key, control in rows:
        fails = sum(x["fail"] for x in cells)
        if "INFO_" in label:
            verdict = ("info: identical setups. A fractal low at i needs bars i+1..i+3 at or above it, so "
                       "knowing it early cannot change a sweep" if fails == 0 else "info: differs")
        elif control:
            verdict = "control caught (expected)" if fails > 0 else "**CONTROL NOT CAUGHT — test is broken**"
            all_ok &= fails > 0
        else:
            verdict = "pass" if fails == 0 else "**FAIL**"
            all_ok &= fails == 0
        extra = [f" ({x['setups_full']} setups)" if "setups_full" in x else "" for x in cells]
        lines.append(f"| {label} | " + " | ".join(f"{x[key]} / {x['fail']}{e}" for x, e in zip(cells, extra))
                     + f" | {verdict} |")
    secs = {c: res[c].get("seconds") for c in COINS}
    head = (f"Run: `research/p2_lookahead.py` once per core coin on the development segment "
            f"(wall time per coin: {', '.join(f'{c[:3]} {s} s' for c, s in secs.items())}). "
            f"**Overall: {'all detectors and generators pass; both controls are caught' if all_ok else 'FAILURES PRESENT'}.**")
    table = BEGIN + "\n" + head + "\n\n" + "\n".join(lines) + "\n" + END
    text = REPORT.read_text(encoding="utf-8")
    if "LOOKAHEAD_TABLE_PLACEHOLDER" in text:
        text = text.replace("LOOKAHEAD_TABLE_PLACEHOLDER", table)
    else:
        a, b = text.index(BEGIN), text.index(END) + len(END)
        text = text[:a] + table + text[b:]
    REPORT.write_text(text, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
