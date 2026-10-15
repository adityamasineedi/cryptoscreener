# CANDLE12_V3_EXEC_FIX/OOS-1: config drift review

**Reviewed:** 2026-10-15, 16:28–16:40 UTC (snapshot of the files at 16:28:35 UTC)
**Contract:** `CANDLE12_V3_EXEC_FIX/OOS-1`, frozen 2026-10-15 15:00:23 UTC
**Resolution:** **C. BLOCK_FORWARD_COLLECTION**. No file restored, and the lock is not rewritten.

## What happened

At 15:56 UTC a second Claude Code session (`b63782ae-be5a-4c10-b213-75f7eb0433ef`) was given a separate task: "Continue the Candle-1 v3 paper-trading implementation". It covers execution modes, restart recovery, a testnet broker-check, the current Binance conditional-order API, data backfill, `freeze-oos`, a drift guard, operational safety, tests and docs. From 16:05 UTC it edited the frozen package. It was still writing files at 16:36 UTC: `binance.py`, `recovery.py`, `broker_check.py` and `tests/test_candle12_v3_paper_ops.py`.

The frozen versions were recovered from Claude Code's file-history snapshots. Each one is **hash-verified** against the lock and stored in `frozen/`. The 16:28 UTC versions of the changed files are in `current/`, and the full unified diffs are in `*.diff`.

## Classification

| File | Frozen sha256[:16] | 16:28 sha256[:16] | Diff | Class | Why |
|---|---|---|---|---|---|
| adapter.py | 7c1f0a00d8117ef1 | a797cfba60720a1e | +250 −12 | INCOMPLETE_CHANGE | Intentional (task items 2 and 8): write-ahead order persistence, restart `resume`, halt and safety limits. Still being edited. Safety limits can block entries but never change price or size. |
| binance.py | 07ee8a5507e4720e | 4711adb38856435c | +236 −38 | INCOMPLETE_CHANGE | Intentional (items 3 and 4): conditional-order endpoint and capabilities. Rewritten again after the snapshot (16:32 UTC). |
| broker.py | ec7ab127f34cd2f7 | d6c9da21029a927f | +67 −2 | INCOMPLETE_CHANGE | Intentional (item 2): venue lookup, open orders, snapshot and restore, duplicate client id, unknown-state timeouts. |
| store.py | c869ff38a1094b5a | 2b331f1e8ceaf7f2 | +48 −6 | INCOMPLETE_CHANGE | Intentional (items 1 and 8): execution-mode stores, alerts, heartbeats. |
| contract.py | 745b8cf82605d0ba | e32794126a2cd0b8 | +24 −0 | INCOMPLETE_CHANGE | Intentional (items 1, 6, 7): allowed symbols, OOS mode, contract and lock hashes; eight new files added to the fingerprint list. Changed twice during the review. |
| oos.py | 24f3ede8edd1f747 | a80c61e5b50fe2b0 | +7 −2 | INCOMPLETE_CHANGE | Intentional (item 1): only a `virtual_paper_mainnet` store created OOS-eligible can be read. Thresholds and classification unchanged. |
| cli.py | 3bd5dd7da06c9266 | 70e4feda0de1cd45 | +267 −38 | INCOMPLETE_CHANGE | Intentional (items 1, 3, 5, 6, 8): new commands. Being edited at the time of the review. |

There are also ten new files: `backfill.py`, `broker_capabilities.json`, `broker_check.py`, `capabilities.py`, `freeze.py`, `guard.py`, `modes.py`, `recovery.py`, `safety.py`, `status.py`.

- **Accidental noise:** none.
- **Unexplained drift:** none. Every change traces back to the second session's brief.
- **Strategy logic, entry, stop, target, sizing, fees, slippage and OOS thresholds:** not changed in any diff reviewed. The OOS start stays 2026-10-16 and the earliest read stays 2027-10-16; `freeze.py` keeps both.

## Why C, not A or B

- **A. Restore frozen state.** This would delete work you requested an hour ago, while that session is still writing. It would re-apply its edits after the restore, so drift would come back with a half-merged package.
- **B. Refreeze as a v2 contract.** That needs every change to be intentional, reviewed, tested and necessary. The changes are intentional, but they are still in progress. They have not been reviewed as a whole, and the other session has not reported completion. It also plans its own `freeze-oos`, which:
  - **rewrites the lock in place under the same contract id (`OOS-1`)**, instead of a new version with the old lock kept as SUPERSEDED;
  - needs a clean git working tree, which this repository does not have (hundreds of unrelated uncommitted changes).
- **C. Block forward collection** is the only option consistent with the rules.

## How the block is enforced (verified 16:31 UTC)

- **The other session's own guard** (`guard.preflight("virtual_paper_mainnet")`) returns `eligible: false` with codes `NOT_FROZEN`, `CONFIG_DRIFT`, `CODE_DRIFT`. An OOS-eligible run cannot start or resume.
- **`contract.drift(lock)`** reports 7 changed files plus `config_fingerprint` and `file_list`. The OOS reader classifies any read `CONFIG_DRIFT`. It also refuses every store that is not a `virtual_paper_mainnet` store created OOS-eligible.
- **The original OOS-1 lock and contract text are preserved byte-for-byte:**
  - `candle12_v3_oos_contract.lock.OOS-1.json` (sha256 `82f54fc7…e599`);
  - `candle12_v3_oos_contract.OOS-1.md`.

## To lift the block (before 2026-10-16 00:00 UTC, or with a new OOS start)

1. Let session `b63782ae` finish and report. Stop any further edits to `candle12_v3_paper/`.
2. Review the final diffs against `frozen/`. Run every CANDLE12 suite (at 16:40 UTC: 87 original + 60 ops tests passing).
3. Refreeze as **`CANDLE12_V3_EXEC_FIX/OOS-2`**:
   - new contract id in `contract.py`;
   - the OOS-1 lock kept as SUPERSEDED (copy above);
   - the diff, rationale, test results, fingerprints and timestamp recorded.

   Do not use the in-place `freeze-oos` rewrite of OOS-1.
4. If step 3 cannot finish before 2026-10-16 00:00 UTC, OOS-2 needs a later OOS start (and a later earliest read) written into the contract before it is frozen.
