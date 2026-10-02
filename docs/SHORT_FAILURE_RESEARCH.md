# Short Failure Research — LH + Bearish BOS (COMBO_02 SHORT)

**Status:** Research note — **shorts not endorsed** as a playbook  
**Window:** last ~1200 bars per symbol/TF (BTC / ETH / SOL · 15m + 1h)  
**Path tested:** same as longs — **Trend + BOS** (`COMBO_02`), direction **SHORT** (LH+LL + bearish BOS)  
**Capital framing:** `$1,000` · **2% risk** (`$20` = 1R)

> Historical research only. Explains why the mirrored short plan failed while HL longs worked on the same data.

---

## 1. Verdict

**LH shorts failed because the sample sat in a strong bullish drift, not because stops were instantly wrong.**

| Side | n | Win rate | Avg R | Outcomes | P&L @ $20/R |
|------|---|----------|-------|----------|-------------|
| **LONG (HL)** | 24 | ~67%* | **+1.18** | mostly TP1 | **~+$566** |
| **SHORT (LH)** | 35 | **14.3%** | **−0.47** | **30 SL / 5 TP1** | **~−$327** |

\*Long win rate from per-row summaries (mixed TFs); short from full trade list.

Same engine, same min R:R gate, opposite structure filter → **opposite expectancy**.

---

## 2. What was tested

Mirror of the long research path:

1. Setup TF trend = **BEARISH** (LH + LL)  
2. Setup TF BOS = **CONFIRMED** + **BEARISH_BOS**  
3. Entry / stop / TP1 from structure; **TP1 R ≥ min_rr** (default 2.0)  
4. No impulse/pullback gate (those combos printed **0** ACTIVE trades)

Script: `backend/scripts/research_short_failures.py` → `_short_failure_research.json`.

---

## 3. Regime (primary cause)

Close-to-close drift over the same ~1200-bar tails:

| Symbol | 15m change | 1h change |
|--------|------------|-----------|
| BTCUSDT | **+3.4%** | **+31%** |
| ETHUSDT | **+3.7%** | **+43%** |
| SOLUSDT | **+6.1%** | **+55%** |

Higher-TF path was a **persistent uptrend**. Local LH + bearish BOS still fires (mean-reversion / counter-swings), but **continuation favored longs**. Shorts were repeatedly shorting into that drift.

**Root cause #1 — wrong regime for the thesis**, not a broken short math path.

---

## 4. Trade pathology (not a “next-bar stop” bug)

| Metric (SHORT, n=35) | Value | Meaning |
|----------------------|-------|---------|
| Stop hit on **next bar** | **1 / 35** | Almost never instant invalidation |
| Avg stop distance | ~1.75% | Stops not absurdly tight |
| SL rate | **30 / 35 (86%)** | Thesis dies before TP1 |
| Avg MFE (all) | ~1.06R | Some room in favor |
| Avg MAE (all) | ~1.33R | Adverse exceeds favorable |
| MFE ≥ 1R | 40% | Partial progress common |
| MFE ≥ 2R | **20%** | Full TP1 excursion rare |
| Losers’ avg MFE | **0.76R** | Often “worked a bit” then failed |
| Losers’ avg MAE | **1.45R** | Then ran through stop |
| Losers never even +0.5R MFE | **12 / 30** | True dead shorts ~40% of losses |
| When win: avg win R | **~+2.73R** | Payoff OK when right |
| Expectancy | 0.143×2.73 − 0.857×1.0 ≈ **−0.47R** | Hit-rate too low |

**Root cause #2 — failure mode is delayed thesis failure** (price dips, then resumes higher), not entry-bar stop hunting.

**Root cause #3 — asymmetric sample:** more short signals (35) than long (24) in a rising market → over-firing bearish structure against the tape.

---

## 5. By symbol / TF (shorts)

| Symbol | TF | n | Avg R | Win rate | Notes |
|--------|----|---|-------|----------|-------|
| BTCUSDT | 15m | 2 | −1.00 | 0% | Both SL; MFE &lt; 0.2R |
| ETHUSDT | 15m | 8 | −0.24 | 25% | 6 SL / 2 TP1 |
| SOLUSDT | 15m | 8 | +0.16 | 25% | Only mild +EV pocket |
| BTCUSDT | 1h | 5 | −1.00 | 0% | All SL |
| ETHUSDT | 1h | 5 | −1.00 | 0% | All SL |
| SOLUSDT | 1h | 7 | −0.53 | 14% | 6 SL / 1 TP1 |

1h shorts were especially toxic under the +30–55% drift. SOL 15m was the only slice near flat/slightly green — not enough to carry the plan.

---

## 6. Why longs worked and shorts didn’t (same rules)

| Factor | LONG (HL) | SHORT (LH) |
|--------|-----------|------------|
| Aligns with period drift? | **Yes** | **No** |
| Structure filter | HH+HL | LH+LL |
| Typical outcome | TP1 / TP2 | SL |
| Excursion | High MFE (≥2R often on winners) | MFE stalls &lt;2R then MAE → SL |
| Signal count | Fewer | More (noise against trend) |

The long playbook is **with-trend structure**. The short plan was the **mirror filter in a bull window** — so it acted as a counter-trend fade.

---

## 7. What this is *not*

| Hypothesis | Finding |
|------------|---------|
| Stops too tight / next-bar SL bug | Rejected (1/35 next-bar) |
| TP1 R:R too small | Rejected (min_rr gate; winners ~2.7R) |
| Engine can’t short | Rejected (5 clean TP1s) |
| Impulse/pullback broke shorts | N/A — pullback path was empty for both sides |

---

## 8. Implications for the product / playbook

1. **Do not ship a mirrored LH-short playbook** from this window.  
2. Keep **LONG_STRATEGY.md** as the active structure plan (HL + bullish BOS).  
3. If shorts are revisited later, require **regime filters** first, e.g.:
   - higher-TF trend also bearish (4h/1h), or  
   - rolling drift / EMA slope gate, or  
   - only short when HTF is not printing strong HH+HL  
4. Optional: suppress SHORT `COMBO_02` signals in UI when major/primary TF are bullish (research next, not implemented here).

---

## 9. Reproduce

```bash
cd backend
.\.venv\Scripts\python.exe scripts\research_short_failures.py
.\.venv\Scripts\python.exe scripts\_price_drift.py
```

Artifacts: `scripts/_short_failure_research.json`.

---

## 10. Revision log

| Date | Change |
|------|--------|
| 2026-10-02 | Initial short-failure research: regime drift + MAE/MFE pathology; shorts not endorsed |
