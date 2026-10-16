# CANDLE12 V3: historical robustness study, revised (design only)

**Label on every output:** `HISTORICAL_ROBUSTNESS_ONLY_NOT_OOS_VALIDATION`

**Status:**
- DESIGN_ONLY. Nothing has been run.
- Running it needs review and separate approval.
- This revision replaces the generic grid (target 1.5–2.5R, ATR 0.1–0.3, lookback 18–30, risk filter off/10/15%). That grid was never committed or run.
- The rules below are fixed by this commit, before any result exists.

**Purpose:** falsification and a check that the neighbourhood around the frozen config is stable. It is **not** out-of-sample validation.
- It never selects, promotes or recommends a variant.
- It never changes CANDLE12 or OOS-2.
- The data have all been used before: CANDLE12 was designed, audited and selected on them. A favourable result is therefore an upper bound. Only the negative direction is strong evidence.

## 1. Data scope

- **Bars:** BTCUSDT 1h, 2023-01-01 to 2026-10-15 23:00 UTC (bar open), with 1,000 warm-up bars. No OOS-2 forward bar is loaded.
  - The engine's `check_oos_guard` stays on, and `CANDLE12_V3_ALLOW_OOS` is never set.
  - A loader assert also checks that max bar open ≤ 2026-10-15T23:00Z.
- **Reads:** one read-only database session at start, candles only.
- **Never read:**
  - OOS-2 trades, PnL, R, equity or performance;
  - any file under `backend/data/candle12_v3_paper/`, apart from the allow-listed heartbeat `at` field, used for the resource guard;
  - anything in the LCSC worktree.
- **Never modified:** OOS-2, the 37 fingerprinted files, shared modules, contracts, LCSC.

## 2. Frozen centre and axis mapping

**Frozen book:** `candle12_v3_paper.contract.contract_config()` with the default `SignalConfig()`. This is the OOS-2 config.

**Reproduction reference** (`docs/candle12_v3_exec_fix.md`):
- window ending 2026-10-15 13:00 UTC; 0.02% slippage; funding off;
- 84 trades, net +$601.34, max drawdown $169.35, which is 8.47R at $20.

**Code check of the centre values given in the task:**

| Stated centre | Engine setting | Frozen value | Check |
|---|---|---|---|
| Structure lookback 300 bars | `Candle12V3Config.structure_lookback_bars` (`rolling_structure.DEFAULT_LOOKBACK_BARS`) | 300 | ✔ matches |
| Target: nearest, full exit | `Candle12V3Config.target_policy` | `nearest_target_full_exit` | ✔ matches |
| Stop: structural, "no ATR stop multiplier" | `SignalConfig.sl_buffer_atr`, used by `candle12_v2._structural_stop` → `stop_engine.compute_stop` | **0.2** | ✘ **The stop is structural *with* an ATR buffer.** LONG final stop = min(structural low, Candle-1 low − buffer), where buffer = 0.2 × ATR(14) ([candle12_v2.py:219-224](../backend/app/research/candle12_v2.py#L219-L224), [stop_engine.py:44-45](../backend/app/signals/stop_engine.py#L44-L45)). A zero-buffer stop would not be the frozen book. **The centre used here is 0.2.** |
| Risk-tolerance filter 10% | `Candle12V3Config.risk_tolerance_pct` | 0.10 | ✔ matches |

**Axis mapping.** Each axis is a real, configurable engine setting. The frozen value is at the centre.

| Axis | Engine setting(s) | Kind | Levels (−2, −1, **0**, +1, +2 steps; step = 25% of centre) |
|---|---|---|---|
| L: structure lookback | `structure_lookback_bars` | numeric | 150, 225, **300**, 375, 450 |
| B: stop ATR buffer | `SignalConfig.sl_buffer_atr` | numeric | 0.10, 0.15, **0.20**, 0.25, 0.30 |
| T: target floor | `SignalConfig.min_rr = m` **and** `SignalConfig.tp_r_multiples = (m, m+1, m+2)` | numeric (coupled) | 1.0, 1.5, **2.0**, 2.5, 3.0 |
| R: risk-tolerance filter | `risk_tolerance_pct` | numeric | 0.05, 0.075, **0.10**, 0.125, 0.15 |

**Why the target axis moves two settings together.** `min_rr` only filters *structural* targets. The R-multiple fallbacks (`tp_r_multiples`) are merged without that filter ([target_engine.py:148-176](../backend/app/signals/target_engine.py#L148-L176)), so with `min_rr` alone a 2R fallback TP1 would survive `min_rr = 2.5`. Moving both makes the nearest-target distance actually move. At m = 2.0 the pair is (2.0, (2, 3, 4)), the frozen value exactly.

**Rejected as axes:**

| Proposed or possible axis | Reason |
|---|---|
| Structure lookback 18 / 24 / 30 bars | No 18–30-bar structure setting exists. The real setting is 300, so this grid would exclude the frozen book. |
| `target_policy` → `tp1_runner` | Categorical. It is a different exit strategy, not a neighbour. |
| Stop type (structural vs anything else) | No alternative stop type is configurable. Only the buffer is numeric. |
| `risk_tolerance_pct = None` (filter off) | Categorical on/off, not a step on the numeric axis. |
| `runner_target`, `tp1_fraction`, `breakeven_rule`, `max_hold_bars` | Inactive under `nearest_target_full_exit`. They change nothing. |
| Swing left/right bars, impulse and pullback thresholds | Outside the scope requested. The detector is left at its frozen values. |

**The neighbourhood is one axis at a time.** Exactly one axis moves; the other three stay frozen.
- 4 axes × 4 levels = **16 variants**, plus the frozen point.
- **Immediately adjacent** = the ±1-step variants = **8 variants**. This is the fixed denominator.
- The ±2-step variants form the outer ring. It is reported, and it feeds the "central range" check below.

## 3. Costs and metrics

- **Full costs:**
  - engine fees (0.04% taker each side) and slippage (0.02% primary, 0.05% stress);
  - a flat funding overlay of 0.01% of notional per 8h held, charged to LONGs. Historical settlements cover only 23 of the 84 reference trades, so the overlay is used for every walk.
- **Net-positive** means net PnL > 0 at 0.02% slippage with the overlay.
- **Stress:** the frozen point and the 8 immediate neighbours are also run at 0.05%, and those results are reported.
- **Per variant:** trade count, net $ and R, expectancy (R per trade), profit factor, max drawdown (R).
- **Minimum sample:** a variant with fewer than 50 trades is INSUFFICIENT and counts as **not** net-positive.

## 4. Predeclared rule

**Gate:** the frozen point must reproduce the reference to the cent: 84 trades, +$601.34, $169.35, on the 13:00 window with funding off. Otherwise the verdict is REPRO_FAIL. Stop and report nothing else.

| Verdict | Rule |
|---|---|
| PASS | At least **5 of the 8** immediate neighbours (≥ 60%; 4.8 rounded up) are net-positive, **and** the frozen point is **not an isolated peak**: the median expectancy of the 8 immediate neighbours is ≥ 50% of the frozen point's expectancy. |
| FAIL | The frozen point reproduces, but either condition fails. |
| INCONCLUSIVE | 3 or more of the 8 immediate neighbours are INSUFFICIENT. The rule can't be judged. |

**Also reported** (not part of the verdict):
- the share of net-positive immediate neighbours, out of 8, and the outer ring, out of 8;
- the median neighbourhood expectancy, immediate and all 16;
- **Central range:** whether the frozen expectancy lies between the 25th and 75th percentiles of all 17 points, for example "within the central range" or "above the 75th percentile";
- per-axis one-at-a-time expectancy profiles.

**Not reported:** no best variant, no ranking, no argmax. Tables are in axis and level order. Nothing here may be used to change CANDLE12.

**Power:**
- With about 84 trades and a −1R / +2–4R payoff, one variant's expectancy has a standard error of roughly 0.15R.
- So a neighbour can turn negative or positive by noise alone. PASS means "not obviously fragile", not "robust".

## 5. Random-entry chance baseline

**Not used as an edge test:**
- **Trade-order shuffling:** total profit is unchanged; it moves only the path.
- **Sign-flip:** the fixed-exit payoff is asymmetric, so the symmetric null is false by construction.

**Null books** are matched to the real book:
- same bars, window and warm-up;
- LONG only, one position at a time;
- $20 risk with the same sizing and exchange filters;
- same costs and funding overlay;
- the conservative same-bar rule (stop first); a gap through the stop fills at the bar open;
- each book draws entries until it has the **same trade count** as the real frozen book.

| Variant | Entry | Stop and target |
|---|---|---|
| **S: structural framework** (primary) | uniform random bar after warm-up; entry at the next bar's open + slippage | the frozen functions, called read-only: `compute_stop`, using the most recent *confirmed* swing low (`RollingSwingState`, 3/3) and the Candle-1-equivalent bar low, with the 0.2 × ATR buffer; and `compute_targets` (min_rr 2.0, (2, 3, 4)). Nearest target, full exit. |
| **G: geometry-matched** | same random entries | stop distance (in ATR units) and target R paired from one randomly drawn real trade |

- **Seeds** (predeclared): 1,000 books per variant, seed for book k = 20261016000 + k, k = 0..999.
- **Reported for each variant:**
  - null distributions (median, 5th and 95th percentiles, histogram) of net expectancy, profit factor and max drawdown;
  - the **percentile of the real frozen result** in each;
  - the holding-time distribution, null vs real, so the exposures can be compared.
- **Interpretation** (predeclared, on expectancy in variant S, with G as confirmation):

| Label | Rule |
|---|---|
| ABOVE_CHANCE | Real result ≥ 95th percentile in S and in G |
| WEAK | ≥ 80th percentile in S, but not ABOVE_CHANCE |
| NOT_DISTINGUISHABLE | < 80th percentile in S |

- **Selection bias:** the real book was chosen on these same bars, so its percentile is biased upward. ABOVE_CHANCE is therefore an upper bound, and NOT_DISTINGUISHABLE is strong evidence against an edge. BTC rose over 2023–2026, so the random LONG null is itself positive. Beating it is the point.

## 6. Execution constraints and budget

- **Where it runs:** a separate worktree, `D:/cryptoscreener-worktrees/candle12-robustness`, on a new branch from tag `candle12-v3-oos-2-freeze-2026-10-15`.
  - Study code goes in `backend/scripts/candle12_v3_robustness/`, and output in its git-ignored `out/`.
  - Before starting, its fingerprinted files must match the OOS-2 lock.
  - `D:/cryptoscreener` and the LCSC worktree are never written.
- **How it runs:** one process, serial. No process pool: Windows ProcessPool has a known handle error on this machine. Each walk is appended to a resume file.
- **Hard budget:**
  - **CPU:** BelowNormal priority, pinned to one logical core.
  - **Memory:** the process aborts if RSS exceeds 1 GB, checked between walks and by a watchdog thread every 5 s.
  - **Runtime:** a single walk aborts after 10 minutes; the whole study aborts after 3 hours.
  - **Machine headroom:** it pauses while commit headroom is under 1.5 GB or free memory is under 1 GB.
  - **OOS-2:** it stops if the OOS-2 heartbeat is more than 7 minutes old.
- **Blackouts:** no walk starts if it could overlap these windows (using 1.5 × the slowest timed walk):
  - HH:15–HH:35 UTC: the LCSC hourly run at :20 and the 00:20/08:20/16:20 backfills;
  - HH:58–HH:05 UTC: CANDLE12 bar close.
- **Compute:**
  - 17 walks at 0.02% plus 8 at 0.05% = **25 engine walks** of about 33,000 bars.
  - **Calibration gate:** the reproduction walk is timed. If the projected total exceeds 3 hours, stop and report before continuing.
  - Expected range: 0.5–3 minutes per walk, so about 15–75 minutes plus blackout waits.
  - Null books: the swing state is precomputed in one pass, then 2,000 books are processed in chunks of 100. Estimated 5–20 minutes, under 500 MB.

## 7. Test plan (all pass before any result is reported)

| # | Test | Pass condition |
|---|---|---|
| 1 | Reproduction gate | Exact match to the reference (section 4) |
| 2 | Centre is frozen | The level-0 config equals `contract_config().to_dict()` plus the default `SignalConfig` field by field, with `sl_buffer_atr` 0.2 and `tp_r_multiples` (2, 3, 4) |
| 3 | Axis wiring | Each axis changes only its own setting(s); a ±1 step changes the stop distance or target level on a known synthetic setup |
| 4 | No reserved bars | Max bar ≤ 2026-10-15T23:00Z; the guard raises on a planted 2026-10-16 bar; the allow-env is unset |
| 5 | Freeze and isolation | Guard OK before and after; the `D:/cryptoscreener` tree is unchanged; a path guard raises on any open under `candle12_v3_paper/` (except the heartbeat tail) or the LCSC worktree |
| 6 | Null book matching | Same trade count, LONG only, never two open positions, $20 sizing; the same-bar and gap rules hold on synthetic bars |
| 7 | Null power and calibration | On a synthetic zero-drift walk the real-vs-null percentile is roughly uniform over seeds; a planted edge scores ≥ 95th percentile |
| 8 | Seed determinism | Seeded books repeat byte-for-byte |
| 9 | Budget and blackouts | A synthetic low-headroom, stale-heartbeat, overrun or blackout case pauses or aborts as specified |
| 10 | Output hygiene | Every output carries the label; a scanner finds no best, rank or argmax field |

## 8. Outcome use

Whatever the verdicts:
- OOS-2 continues unchanged to its formal read on or after 2027-10-16;
- no variant is adopted;
- the results are filed for that read as historical context.

A FAIL, INCONCLUSIVE or NOT_DISTINGUISHABLE result is a falsification signal the user may weigh in planning. It is never a reason to tune or to stop OOS-2 early.
