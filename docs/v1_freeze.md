# COMBO_02 v1 freeze — HTF-gated LONG

**Tag:** `v1-combo02-long-htf`  
**Status:** Production candidate (research + paper Path A)  
**Frozen:** 2026-10-03  
**Audit source of truth:** COMBO_02 v1 fidelity audit (blotter 12/12 gate+path PASS; 31 HTF/Path A tests)

## What v1 is

v1 is the **HTF-gated COMBO_02 LONG** playbook on **1h** for **BTCUSDT / ETHUSDT / SOLUSDT** (Binance USDT-M). Entries require setup-TF bullish structure (HH+HL), confirmed bullish BOS, and **4h + 1h both BULLISH** (`HTF_ALIGNED`). Missing HTF, `HTF_NEUTRAL_UNAVAILABLE`, and `HTF_CONFLICT` fail closed (no long). Risk is fixed fractional R with structural stop and TP1 at configured min RR (default 2R). No martingale, no relax-after-loss.

Research/backtest default combo is `COMBO_02` with `require_htf_alignment=True`. Paper/live **Path A** is the only mode labeled **COMBO_02 v1**.

Reference backtest window used in the fidelity audit (2000×1h bars, $20/R, taker 0.04% / maker 0.02%): BTC n=3 WR≈67% avgR≈1.00; ETH n=5 WR≈60% avgR≈0.97; SOL n=4 WR≈50% avgR≈0.52. Negatives: 0 COMBO_02 longs in bearish-4h windows while `COMBO_02_LOCAL` still produced longs.

## Frozen components

| Area | Module / surface | Frozen rule |
|------|------------------|-------------|
| Combo definition | `backend/app/research/bos_combinations.py` | `COMBO_02`: `require_trend=True`, `require_bos=True`, `require_htf_alignment=True`. `COMBO_02_LOCAL`: HTF off, LEGACY/RESEARCH-ONLY |
| HTF gate | `combination_engine.htf_alignment_gate`, `evaluate_combination_at_bar` | LONG only when `classify_htf_alignment == HTF_ALIGNED`; fail closed on missing/neutral/conflict |
| HTF helpers | `bos_strategy_comparison/htf.py` | `htf_trends_for_setup_bar`, `classify_htf_alignment` as used by COMBO_02 |
| Backtest | `combination_backtest.run_combination_backtest` | Passes `candles_1h` / `candles_4h` into eval for HTF combos |
| Research service | `service.walk_forward`, `strategy_matrix`, `detail` | Load HTF when combo requires it |
| Paper Path A | `paper_trade._is_path_a_long`, `on_setup_signal` | Always requires 4h+1h bullish (or `STRONG_LONG`); ENTRY_CANDIDATE does **not** bypass HTF in `path_a` mode |
| Labels | `paper_trade` status + UI panels | Path A = COMBO_02 v1; Path B = experimental (not COMBO_02 v1) |
| Structure engines (as used by v1) | `trend_engine.infer_trend`, `bos_engine.detect_bos`, swing detector | Behavior-preserving only; no “more trades” tweaks on this track |
| Regression tests | `tests/test_combo02_htf_gate.py`, Path A tests in `tests/test_paper_trade.py` | Must pass; failures block unintended v1 edits |

## Do not change (v1)

1. Do **not** weaken or disable the HTF gate for COMBO_02 LONG (no config/research override that allows longs when 4h/1h are not both bullish).
2. Do **not** promote `COMBO_02_LOCAL` to production/default or describe it as HTF-gated v1.
3. Do **not** label Path B or ENTRY_CANDIDATE-only flows as “COMBO_02 v1”.
4. Do **not** change `infer_trend`, `detect_bos`, or swing detection to get more trades on this track — that is a new strategy variant.
5. Do **not** add martingale, relax-after-loss, or aggressive param optimization to v1.
6. Do **not** force chart annotations to equal research blotter entries without going through the same gate functions.
7. Do **not** move the default UI/backtest/job combo off `COMBO_02` without a version bump.

## What is allowed alongside v1

- New combos (`COMBO_03+`), new timeframes, SHORT variants, alternate HTF rules — as **separate** IDs/paths.
- UX: chart overlays from blotter, exports, copy/labels that do not change entry logic.
- Behavior-preserving refactors and performance work (covered by v1 regression tests).
- Paper Path B as an explicit experimental mode (must remain labeled non-v1).
- `COMBO_02_LOCAL` for A/B research baselines only.

## Versioning discipline

| Change type | Action |
|-------------|--------|
| Behavior-changing edit to frozen v1 logic | Bump version (`v2-…` tag), new docs section; do not silently mutate `v1-combo02-long-htf` |
| Experiment | New combo ID and/or new entry path; keep Path A / COMBO_02 v1 untouched |
| Bug fix that restores audited v1 behavior | Allowed on v1 **only** if tests prove fidelity to this freeze + audit |
| Paper/live runs labeled “COMBO_02 v1” | Must match tag `v1-combo02-long-htf` semantics |

**Regression suite (v1-critical):**

```text
backend/tests/test_combo02_htf_gate.py   # research HTF hard gate
backend/tests/test_paper_trade.py        # Path A HTF + ENTRY_CANDIDATE bypass guard
```

Run:

```bash
cd backend
python -m pytest tests/test_combo02_htf_gate.py tests/test_paper_trade.py -q
```

Failures on these files should **block merges** that touch frozen paths unless the change is an intentional version bump with updated docs/tag.

## Related docs

- Production profile (core/secondary risk, monitoring): [`v1_production.md`](./v1_production.md)
- Risk-controlled variant (v1.1 overlay, separate OOS): [`combo02_v1_1_risk_controlled.md`](./combo02_v1_1_risk_controlled.md)
- Playbook overview: [`LONG_STRATEGY.md`](./LONG_STRATEGY.md)
- Changelog entry: [`../CHANGELOG.md`](../CHANGELOG.md)
