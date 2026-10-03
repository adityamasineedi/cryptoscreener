# COMBO_02 v1 production profile

**Freeze:** [`v1_freeze.md`](./v1_freeze.md) · tag `v1-combo02-long-htf`  
**Config module:** `backend/app/research/v1_production.py`  
**Status:** Minimal paper/live-ready book with risk controls (not a profitability claim)

Evidence window used for classification: **2025-01-01 → 2026-01-31**, COMBO_02 LONG, same HTF gate.

---

## 1. Production books

| Symbol | TF | Tier | Risk / trade | Default | Rationale |
|--------|-----|------|--------------|---------|-----------|
| BTCUSDT | 1h | **core** | **1.5%** | ON | Best risk-adjusted (~56% WR, avgR ~+0.66, maxDD ~2.5R, streak ≤2) |
| ETHUSDT | 1h | **secondary** | **0.5%** | ON | Positive but noisy (~38% WR, avgR ~+0.12, maxDD ~10–11R, streak ~10) |
| SOLUSDT | 1h | **secondary** | **0.5%** | ON | Thin sample / modest edge (audit n≈4, avgR ~+0.52) — optional |
| BTCUSDT | 4h | **secondary** (swing) | **1.0%** | OFF | Solid avgR, low n — enable explicitly |
| ETHUSDT | 4h | **secondary** (swing) | **0.5%** | OFF | Positive, higher DD — enable explicitly |
| * | 15m | **research** | **0%** live | OFF | Fee-dominated / negative expectancy |

**Principal reference:** $1,000 → BTC 1h = **$15/R**, ETH/SOL 1h = **$5/R**, BTC 4h = **$10/R**.

### Regime rules (minimal)

1. **Already enforced:** Path A / COMBO_02 LONG only when **4h + 1h are both BULLISH** (`HTF_ALIGNED`); fail closed on missing/neutral/conflict.
2. **No new cross-asset filters** in v1 (e.g. do not gate SOL on BTC 4h) — keep HTF local to the asset.
3. Sub-hour setups remain **research-only** for production claims.

### Note on live setup TF

Live `SignalConfig.mtf_setup` is still **15m** (structure engine). Path A paper applies the **v1 symbol universe + risk %** on top of the existing HTF gate. Research/backtest **claims** for v1 remain on **1h** (core) and optional **4h** (secondary). Aligning live setup evaluation to 1h is a separate follow-up — do not silently change HTF/BOS logic here.

---

## 2. Env / config knobs

| Key | Default | Meaning |
|-----|---------|---------|
| `PAPER_V1_PROFILE_ENABLED` | `true` | Apply universe + per-symbol risk on Path A |
| `PAPER_V1_UNIVERSE_ONLY` | `true` | Path A only BTC/ETH/SOL |
| `PAPER_V1_SECONDARY_ENABLED` | `true` | Allow ETH/SOL at 0.5%; set `false` for BTC-only core book |

Code constants: `V1_BOOKS`, `V1_PAPER_RISK_BY_SYMBOL` in `v1_production.py`.

---

## 3. Monitoring plan (weekly)

### Metrics per asset × TF

Win rate, avg R, net PnL, max DD (R), max losing streak, trade count. Compare to backtest reference ranges in `V1_MONITOR_THRESHOLDS`.

### Review thresholds (after ≥10 closed trades)

- Realized WR **&lt; 70%** of backtest WR → flag for review (do not auto-tweak params).
- Max DD **&gt; 1.5×** backtest max DD → pause or cut size (e.g. secondary off, or BTC risk → 1%).
- Max losing streak **&gt; 1.5×** backtest streak → same as DD rule.

### Logging (required fields)

Each paper open/close should retain: asset, timeframe, combo (`COMBO_02`), path (`PATH_A`/`PATH_B`), entry/exit/stop/TP1, outcome, R, fees/`risk_usd`, and HTF (`trend_1h`, `trend_4h`, `htf_alignment`) in `signal_snippet`.

### Helper

```bash
cd backend
python scripts/v1_monitor_paper.py
# optional: --json path/to/closed_trades.json
```

---

## 4. Do not do next

1. Do **not** “fix” ETH 1h by weakening HTF or changing swing/trend/BOS to get more trades.
2. Do **not** add 15m (or any sub-hour) as a live production timeframe.
3. Do **not** start heavy parameter optimization / curve-fitting on ETH or SOL.
4. Do **not** mix Path B or `COMBO_02_LOCAL` into v1 production claims or labels.
5. Do **not** raise ETH/SOL risk to BTC core levels without a new evidence window and version bump.
6. Do **not** silently mutate frozen HTF gate behavior — see `v1_freeze.md`.

---

## Related

- Freeze: [`v1_freeze.md`](./v1_freeze.md)
- Playbook: [`LONG_STRATEGY.md`](./LONG_STRATEGY.md)
- Changelog: [`../CHANGELOG.md`](../CHANGELOG.md)
