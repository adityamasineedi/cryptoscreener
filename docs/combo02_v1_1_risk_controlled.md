# COMBO_02_V1_1_RISK_CONTROLLED

**Strategy ID:** `COMBO_02_V1_1_RISK_CONTROLLED`  
**Parent:** `COMBO_02_V1` (`v1-combo02-long-htf`) — **archived OOS_FAIL** on 2025-07-01 → 2026-09-30  
**Variant version:** `v1.1-combo02-long-htf-risk-controlled`  
**Direction:** LONG only  
**Symbols:** BTCUSDT, ETHUSDT, SOLUSDT  
**Setup TF:** 1h · **HTF:** hard 1h + 4h bullish alignment  

## Purpose

Separate, versioned portfolio risk overlay on **unchanged** COMBO_02 / COMBO_02_V1 signal logic.
Does **not** modify swing detection, BOS, HTF gate, stop, TP, or historical v1 artifacts.

## Signal rules (inherited — frozen)

Reuse COMBO_02_V1 exactly:

- Setup-TF bullish structure (HH+HL)
- Confirmed bullish BOS
- Hard 4h+1h `HTF_ALIGNED` (fail closed)
- Structural stop + TP1 ≥ 2R

No new filters, indicators, or entry variations.

## Risk-control changes only

| # | Control | Spec |
|---|---------|------|
| 1 | Per-symbol risk | Frozen evidence-era profile: **BTC 1.5%**, **ETH 0.5%**, **SOL 0.5%** (not flattened to 2%) |
| 2 | Cluster heat | Cap **4%** equity across BTC/ETH/SOL → reject `CLUSTER_HEAT_EXCEEDED` |
| 3–4 | Max concurrent | Max **2** open positions → third entry rejects with `MAX_CONCURRENT_EXCEEDED` |
| 5 | Daily loss halt | Halt new entries at **−2R** day PnL |
| 6 | Symbol streak pause | After **3** consecutive −1R losses → pause symbol **24h** |
| 7 | Strategy DD halt | Stop new entries at **6R** drawdown from strategy peak |
| 8 | Symbol DD halt | Pause symbol after **4R** drawdown from its local peak |
| 9 | Halt semantics | Block **new entries only** — never force-close |
| 10 | Rejection audit | Every blocked entry records an explicit reason |

Rejection reasons are **separate**:

- `MAX_CONCURRENT_EXCEEDED` — third concurrent BTC/ETH/SOL entry blocked
- `CLUSTER_HEAT_EXCEEDED` — new entry would push combined open risk above 4% equity

Implementation: `backend/app/research/combo02_v1_1_risk_controlled.py`

## Predeclared OOS windows

Disjoint from:

| Partition | Window |
|-----------|--------|
| Base | 2022-10-05 → 2024-12-31 |
| OOS development | 2025-01-01 → 2025-06-30 |
| Failed v1 OOS | 2025-07-01 → 2026-09-30 |

| Window | Range (UTC) | Role |
|--------|-------------|------|
| **Formal acceptance** | `2026-10-06 00:00:00` → `2027-03-31 23:59:59` | Only window allowed for READY_FOR_PAPER / pass/fail |
| **Operational smoke** | `2026-10-01` → `2026-10-05` | Smoke / wiring only — **not** acceptance evidence |
| Failed v1 OOS (diagnostic) | `2025-07-01` → `2026-09-30` | “Would risk controls have altered that path?” only — **never** acceptance |

### Smoke result (non-acceptance)

The 2026-10-01 → 2026-10-05 smoke run returned `INSUFFICIENT_SAMPLE` (0 trades; completeness ~83–84%).  
That result is **explicitly not acceptance evidence** for v1.1.

Formal OOS `2026-10-06` → `2027-03-31` has **not** been evaluated yet (window not elapsed).

## Acceptance criteria

Performance (only after validation-state guards pass):

- Combined max drawdown ≤ 6R
- Combined average net R > 0
- Max losing streak ≤ 8
- No silent gate skips
- No parameter tuning during validation

### Minimum-sample rules

| Combined closed trades | Classification (if guards + integrity pass) |
|------------------------|-----------------------------------------------|
| ≥ 30 | `READY_FOR_PAPER` |
| 15–29 | `OOS_PASS_WITH_REVIEW` only |
| < 15 | `INSUFFICIENT_SAMPLE` |

Per-symbol claims require ≥ **20** closed trades on that symbol.

### Validation-state guards (do not classify pass/fail)

- Do **not** run / classify until the window **start time has been reached** (`WINDOW_NOT_STARTED` → disabled)
- Do **not** classify until the window **end time has passed**
- Do **not** classify as pass/fail if 1h or 4h completeness < **99%**
- Do **not** classify as pass/fail if lookahead validation fails
- Do **not** use the failed parent OOS window for v1.1 acceptance

## Status labels

| Label | Meaning |
|-------|---------|
| `READY_FOR_PAPER` | Formal OOS met acceptance + ≥30 combined closed trades |
| `OOS_PASS_WITH_REVIEW` | Formal OOS integrity/performance pass with 15–29 combined trades |
| `BLOCKED` | Hard performance/integrity fail with adequate sample (≥15) |
| `INSUFFICIENT_SAMPLE` | Window not elapsed, data/lookahead incomplete, or <15 combined trades |

## What this variant must not do

- Mutate COMBO_02_V1 signal / stop / TP code paths
- Create paper or live trades as part of validation
- Reuse the failed OOS window for acceptance
- Treat the five-day smoke window as acceptance evidence
- Silently flatten per-symbol risk to 2%
- Collapse `MAX_CONCURRENT_EXCEEDED` into `CLUSTER_HEAT_EXCEEDED`

## Related

- Parent freeze: [`v1_freeze.md`](./v1_freeze.md)
- Parent production profile: [`v1_production.md`](./v1_production.md)
- Parent OOS (archived fail): `backend/reports/combo02_v1_oos/`
- v1.1 OOS reports: `backend/reports/combo02_v1_1_risk_controlled_oos/`
- Unit tests: `backend/tests/test_combo02_v1_1_risk_controlled.py`
- V1 unchanged regression: `backend/tests/test_combo02_v1_unchanged_regression.py`
