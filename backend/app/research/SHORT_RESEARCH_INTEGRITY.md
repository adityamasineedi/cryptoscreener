# SHORT Research Pipeline Integrity Record

Non-secret recovery and validation stamp for the COMBO_02 SHORT research pipeline.

## Recovery

| Field | Value |
| --- | --- |
| Recovered source | `git stash@{0}` untracked tree (`stash@{0}^3`) |
| Recovery hash | `81a4bf5f0cf590e6ff8ca3d378fce68c126219ff` |
| Stash message | `WIP: SHORT research (combo02/entry/diagnostics/pullback)` |
| Module path | `backend/app/research/combo02_short_research.py` |
| Module line count | 721 |
| Module kind | `FULL_PIPELINE` (not a boundary stub) |
| Integrity stamp | `combo02_short_research.full.v1` |
| Restored direction tests | `backend/tests/test_direction_primitives.py` (362 lines) |
| Integrity tests | `backend/tests/test_short_research_integrity.py` |

## Validation

| Field | Value |
| --- | --- |
| Validation date (UTC) | 2026-10-04 |
| Backend tests | 224 passed (SHORT + direction + integrity + v1 suite) |
| Frontend tests | 137 passed |
| LONG regression | PASS |
| COMBO_02 v1 regression | PASS |
| SHORT isolation | PASS |

## Operational safety status

| Flag | Value |
| --- | --- |
| `strategy_id` | `COMBO_02_SHORT_RESEARCH` |
| `combo_version` | `v2-short-research` |
| `source` | `SHORT_RESEARCH_PIPELINE` |
| `direction` | `SHORT` |
| `paper_eligible` | `false` |
| `production_approved` | `false` |
| `telegram_eligible` | `false` |
| COMBO_02 v1 | LONG-only (unchanged) |
| BTC/ETH/SOL v1 | unchanged |

## Distinction

- **SHORT isolation available** — paper / live / production / Telegram blocked.
- **SHORT research pipeline available** — full research module present (`SHORT_RESEARCH_PIPELINE_COMPLETE=True`).

These are not the same. A boundary-only stub must not claim full pipeline availability and must surface `short_research_module_incomplete`.

## Historical artifacts

Preserved (not regenerated or overwritten during recovery):

- `backend/reports/short_research/short_entry_research_20261004T095422Z-24f02156.json`
- `backend/reports/short_research/short_pullback_rejection_20261004T112702Z-305889de.json`
