# Long Strategy — Structure HL (Higher High + Higher Low)

**Status:** Working playbook for **long-only** setups  
**Market:** Binance USDT-M perpetual futures  
**Engine:** Structure-based setup signals (not prediction, not auto-execution)  
**Capital example used in research:** `$1,000` · **risk per trade:** `2%` (`$20` = 1R)  
**v1 freeze:** [`v1_freeze.md`](./v1_freeze.md) · git tag `v1-combo02-long-htf`  
**v1 production profile:** [`v1_production.md`](./v1_production.md) (core/secondary risk books)

> Historical research only. Not a claim of future profitability.  
> Live UI states are **ENTRY candidates** — never automatic BUY orders.

---

## 1. Purpose

Take **long** trades only when bullish market structure is confirmed:

- **HH** (Higher High) + **HL** (Higher Low) on the setup timeframe  
- **Bullish BOS** (Break of Structure) in the direction of that trend  
- Risk defined by structure; first target at least **min R:R** (default **2R**)

Do **not** long into LH+LL (bearish) structure.

---

## 2. Structure definition (mandatory)

From confirmed swings only (never candle color):

| Label | Meaning |
|-------|---------|
| **HH** | Latest swing high above prior swing high |
| **HL** | Latest swing low above prior swing low |

**Bullish trend (LONG context)** when the last two highs/lows satisfy:

- last high label = `HH` and price > prior high  
- last low label = `HL` and price > prior low  

Reason string from engine: `"Higher High + Higher Low"`.

If structure is `NEUTRAL`, `BEARISH`, `INSUFFICIENT_DATA`, or `WAITING` → **no long**.

---

## 3. Timeframes

| Role | Default TF | Long requirement |
|------|------------|------------------|
| Major | 4h | Prefer BULLISH (full path) |
| Primary | 1h | Prefer BULLISH (full path) |
| Setup | 15m | **Must** show HH+HL + bullish BOS |
| Entry refine | 5m / 1m | Optional timing |

**Research path (COMBO_02):** setup TF Trend + BOS **plus hard 4h/1h HTF alignment** (HL longs).  
**Legacy A/B (`COMBO_02_LOCAL`):** setup TF Trend + BOS only (no HTF) — research comparison only.  
**Full live path:** also requires impulse → pullback → retest + MTF alignment (stricter; fewer trades).

---

## 4. Entry rules

### 4.1 Allowed long path A — Trend + BOS + HTF (research default)

`COMBO_02` / Path A now hard-requires higher-timeframe alignment (fail closed):

1. Setup TF trend = **BULLISH** (HH + HL)  
2. Setup TF BOS = **CONFIRMED** + direction **BULLISH_BOS**  
3. **4h trend = BULLISH AND 1h trend = BULLISH** at signal time (closed bars only)  
4. Entry = retest of broken level if available, else market at close (engine rule)  
5. Stop + targets computable  
6. **TP1 R ≥ min_rr** (default **2.0**) — micro structural targets are skipped  

Missing / mixed / conflicting HTF → **no long**.  
Legacy setup-TF-only Path A is retained as `COMBO_02_LOCAL` for A/B research only.

**Do not** take BOS-only without bullish HL trend.

### 4.2 Preferred long path B — Full setup (live Trade Plan)

All of path A, plus:

| Gate | Pass condition |
|------|----------------|
| MTF | 4h + 1h bullish (no conflict with long) |
| Impulse | Valid bullish displacement after BOS |
| Pullback | `ACTIVE` or `CONFIRMED`, structure intact |
| Retest | Retest confirmed **or** pullback `CONFIRMED` |
| Volume | RVOL ≥ threshold when used as hard gate |
| Risk / targets / R:R | Stop exists; TP1 ≥ min_rr |

Live status when ready: **`LONG_ENTRY_CANDIDATE`** (not “BUY”).

### 4.3 Known gap (pullback)

On recent history, pullback often stays **`WAITING`** (“Waiting for valid impulse after BOS”).  
Until impulse confirms and bars print after it, **path B will not fire**.  
That is a data/sequence dependency — do not fake pullback PASS.

---

## 5. Stop loss (long)

Structural stop only (no fixed % invent):

1. Prefer pullback / impulse origin / last demand swing below entry  
2. Apply ATR buffer (`sl_buffer_atr`, default `0.2`)  
3. Final stop must be **below** entry  

If stop cannot be computed → **NO_SETUP**.

**Risk amount:**  
`risk_$ = account_equity × risk_percent`  
Example: `$1,000 × 2% = $20` (1R).

Position size from stop distance (engine calculator) — never size from hope.

---

## 6. Take profit (long)

| Target | Rule |
|--------|------|
| **TP1** | First structural level **≥ min_rr** (default 2R), else R-multiple 2R |
| **TP2** | Next structural / 3R |
| **TP3** | Further structural / 4R (optional) |

**Hard rule:** TP1 must not be a micro swing (e.g. 0.1R).  
Management research exits at **first target hit**; gating on TP2/TP3 alone is forbidden.

R:R **PASS** = `TP1_R ≥ min_rr`.

---

## 7. Invalidation (exit / cancel long)

Cancel or treat as invalid when any of:

- Setup trend flips away from BULLISH (HL broken → LL / bearish sequence)  
- Pullback state = **INVALIDATED** (close below impulse origin)  
- Bearish CHOCH / opposing BOS against the long  
- MTF conflict (higher TF bearish vs long setup)  
- Stop hit (−1R)

---

## 8. Checklist before clicking long

```
[ ] Setup TF: HH + HL (BULLISH)
[ ] Confirmed BULLISH_BOS
[ ] (Full path) Impulse PASS
[ ] (Full path) Pullback ACTIVE/CONFIRMED + structure intact
[ ] (Full path) Retest PASS or pullback CONFIRMED
[ ] (Full path) MTF not CONFLICT / HTF not bearish
[ ] Stop below entry (structural)
[ ] TP1 ≥ 2R
[ ] Size = 2% risk / (entry − stop)
[ ] Not longing into LH+LL
```

---

## 9. Position sizing example

| Field | Value |
|-------|-------|
| Equity | $1,000 |
| Risk % | 2% |
| 1R | $20 |
| Entry | 2,700 |
| Stop | 2,660 |
| Risk/unit | $40 |
| Qty | 20 / 40 = **0.5** contracts (illustrative) |
| TP1 (2R) | 2,700 + 2×40 = **2,780** |

Win at TP1 ≈ +$40 (+2R). Stop ≈ −$20 (−1R).

---

## 10. Research notes (recent sample)

Window: last ~1200 bars · BTC / ETH / SOL · 15m & 1h · after TP1 min-R fix  
Playbook measured: **TREND_BOS · LONG only (HL context)** · $20/R

| Symbol | TF | n | Avg R | TP1 hit | SL | P&L @ $20/R |
|--------|----|---|-------|---------|----|-------------|
| BTCUSDT | 15m | 3 | +2.33 | 67% | 0% | **+$140** |
| ETHUSDT | 15m | 3 | +2.02 | 100% | 0% | **+$121** |
| SOLUSDT | 15m | 4 | +0.50 | 50% | 50% | **+$40** |
| BTCUSDT | 1h | 9 | +0.70 | 56% | 44% | **+$126** |
| ETHUSDT | 1h | 2 | +2.00 | 100% | 0% | **+$80** |
| SOLUSDT | 1h | 3 | +1.00 | 67% | 33% | **+$60** |

**Contrast:** same window, **LH shorts** were −EV (−0.47R avg, 30/35 SL) — see `docs/SHORT_FAILURE_RESEARCH.md`.  
**Contrast:** BOS-only (no HL trend filter) lost money earlier.  
**Contrast:** impulse+pullback combos → **0 trades** (pullback stuck WAITING).

Small sample — treat as directional bias for the playbook, not proof.

---

## 11. What this strategy explicitly rejects

| Reject | Why |
|--------|-----|
| Long on LH+LL / bearish BOS | Against structure |
| BOS-only without HL trend | Noisy; lost in research |
| TP1 &lt; min_rr | High hit-rate, negative expectancy |
| Fabricated OHLCV / zones / liq | Hard product rule |
| Converting WAITING → BUY | Honesty / no fake readiness |

---

## 12. Implementation map (code)

| Concern | Location |
|---------|----------|
| HH/HL trend | `backend/app/signals/trend_engine.py` |
| BOS | `backend/app/signals/` BOS engine + `signal_engine.py` |
| Impulse / pullback / entry | `impulse_engine.py`, `pullback_engine.py`, `entry_engine.py` |
| Stop / targets / R:R | `stop_engine.py`, `target_engine.py`, `risk_engine.py` |
| Config (`min_rr`, MTF, RVOL) | `backend/app/signals/config.py` |
| Research combos | `backend/app/research/bos_combinations.py` (`COMBO_02` = Trend+BOS+HTF; `COMBO_02_LOCAL` = setup-TF only) |
| HTF hard gate | `combination_engine.htf_alignment_gate` + `bos_strategy_comparison/htf.py` |
| Opt-in research gates (default OFF) | `backend/app/signals/research_gate.py` + `RESEARCH_GATE_*` env |
| UI Trade Plan | `frontend/src/tradePlan/`, SETUP / TRADE PLAN tabs |

---

## 13. Operator workflow in the app

1. Futures screener → filter **Signal BUY / Setup** when useful  
2. Open coin → **SETUP** + **TRADE PLAN**  
3. Confirm **BULLISH** trend (HL) + setup state  
4. Only act on **LONG_ENTRY_CANDIDATE** / ENTRY READY when full path confirms  
5. Size from Trade Plan risk fields (2% of equity)  
6. Manage to TP1 (≥2R) / stop; invalidate if structure breaks  
7. **Backtest** tab → Path A (COMBO_02 LONG) with lookback presets (12d–max) on real OHLCV

---

## 14. Revision log

| Date | Change |
|------|--------|
| 2026-10-02 | Initial long playbook: HL + bullish BOS; TP1 ≥ min_rr; research note on HL longs vs LH shorts; pullback WAITING gap documented |
| 2026-10-02 | Link short-failure research (`SHORT_FAILURE_RESEARCH.md`): bull drift + delayed thesis failure, not stop bug |
| 2026-10-02 | Opt-in `RESEARCH_GATE_*` flags (default false). SHORT-policy study may suggest `BLOCK_SHORTS` after larger sample review — not enabled live by default |
