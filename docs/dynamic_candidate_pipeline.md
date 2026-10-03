# Dynamic COMBO_02 v2 candidate pipeline

Future research/paper framework. **Does not change frozen COMBO_02 v1.**

## Identity

| Field | Value |
|-------|-------|
| strategy_id | `COMBO_02_V2_RESEARCH` |
| combo_version | `v2-research` |
| source (registry) | `DYNAMIC_CANDIDATE_PIPELINE` |
| source (watcher) | `V2_CANDIDATE_PAPER_WATCHER` |
| telegram_eligible | always `false` |

## Pipeline

1. **Discovery** (daily/manual) → `DISCOVERED`
2. **Data health** (1h + 4h, ≥540d, ≥99%) → `DATA_READY`
3. **Frozen COMBO_02 research** → `PROMISING` / `RESEARCH_REJECTED`
4. **OOS + portfolio** → `V2_PAPER_CANDIDATE` / `OOS_FAILED`
5. **Operator API** `POST /api/research/candidates/{symbol}/approve-paper` → `PAPER_VALIDATING`
6. **V2CandidatePaperWatcher** (default OFF) may paper-trade only `PAPER_VALIDATING` + approved rows

## Commands

```bash
# Discovery
.\.venv\Scripts\python.exe scripts/run_dynamic_candidate_discovery.py --top-n 30

# One-shot: data-health → frozen COMBO_02 backtest → OOS
.\.venv\Scripts\python.exe scripts/advance_dynamic_candidates.py
.\.venv\Scripts\python.exe scripts/advance_dynamic_candidates.py --symbol BNBUSDT
.\.venv\Scripts\python.exe scripts/advance_dynamic_candidates.py --skip-oos

# Tests
.\.venv\Scripts\python.exe -m pytest tests/test_dynamic_candidate_pipeline.py -q
```

UI: Backtest → **Dynamic Candidate Pipeline** → **Run research advance**.

API: `POST /api/research/candidates/advance`

## Config (defaults fail closed)

```
DYNAMIC_CANDIDATE_DISCOVERY_ENABLED=false
DYNAMIC_V2_PAPER_WATCHER_ENABLED=false
DYNAMIC_V2_MAX_OPEN_POSITIONS=1
DYNAMIC_V2_MAX_TOTAL_RISK_PERCENT=0.005
```

## Safety

Frozen COMBO_02 v1 remains BTC/ETH/SOL only.
Dynamic screener candidates cannot create v1 paper trades,
cannot become Telegram eligible, and cannot be promoted
without explicit operator approval and separate v2 validation.
