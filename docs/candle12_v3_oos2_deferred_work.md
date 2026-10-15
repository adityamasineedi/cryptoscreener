# CANDLE12_V3_EXEC_FIX/OOS-2: deferred work and branch policy

Written 2026-10-15, before the OOS window opens. None of these items is fixed during the window.

Every file named below except `docs/candle12_v3_oos_contract.*` is one of the 37 fingerprinted files frozen in `docs/candle12_v3_oos_contract.lock.json` (tag `candle12-v3-oos-2-freeze-2026-10-15`). A change to any of them is CODE_DRIFT. The running adapter halts within 5 minutes, and the OOS-2 window is void. So every fix below waits until after the OOS-2 read (earliest 2027-10-16), or goes into a new contract version.

## Deferred items

| # | Item | Current behaviour | Risk during OOS-2 | Why deferred | Required post-OOS fix | New contract version? |
|---|---|---|---|---|---|---|
| D1 | `backfill-data` exit code (`backfill.py`, `cli.py`) | Exits 0 when a symbol reports `GAPS`. Only conflicts and fetch errors set `ok: false`, which gives exit 1. | Low. The runbook says to read the JSON, never the exit code. Preflight's own continuity checks still fail on gaps. | Fingerprinted file. | Exit non-zero on `GAPS`. Add `--allow-gaps` for deliberate partial ranges. | Yes, if done before the read: CODE_DRIFT. After the read it belongs to the next version. |
| D2 | Testnet client, non-JSON 4xx (`binance.py`) | `resp.json()` raises on an HTML or empty 4xx body, as an uncaught exception, not `BrokerRejected`. | None for OOS-2: the virtual run never uses `binance.py`. It can crash `broker-check` or a testnet run. | Fingerprinted file, and not on the OOS path. | Parse defensively. Map to `BrokerRejected(ORDER_REJECTED, None, text)` or `BrokerUnavailable` on 5xx. Add a test. | Yes (CODE_DRIFT) if changed in this checkout. A testnet-only change can live on a branch with its own non-OOS checkout. |
| D3 | Unknown algo status maps to `TRIGGERED` (`binance.py`) | Any `algoStatus` other than NEW, CANCELED, EXPIRED or REJECTED is treated as triggered and read through `actualOrderId`. | None for OOS-2 (testnet only). On testnet, a new status could be misread until `broker-check` evidence pins the enum. | Fingerprinted file. The real enum is undocumented. | Record observed statuses with the broker-check evidence template. Map them explicitly; unknown becomes `UNKNOWN`, which halts. | Same as D2. |
| D4 | Funding grid fixed at 8 h (`adapter._check_funding` uses `funding_interval_hours`; `backfill.funding_gaps` 8 h) | Missing-funding detection assumes 00/08/16 UTC settlements. | Medium-low. If Binance changes BTCUSDT to a 4 h or 1 h interval, extra settlements are not checked, and an 8 h slot may be reported missing. Either way it surfaces as an open exception (fail-safe), not as silent PnL. | Fingerprinted files (the interval is part of the frozen config). | Derive the interval from `fundingInfo` / settlement history; a change becomes an explicit exception. | Yes. The funding interval is a contract term. |
| D5 | Kill-switch release not logged (`safety.py`, `cli.py`) | `engage` writes who and why into the KILL file; `release` deletes it with no record. | Low. A missed signal while engaged is already an exception. Only the release time is unrecorded. | Fingerprinted files. | Append engage and release rows to an audit log (and to the store when one is given). | Yes (CODE_DRIFT). Until then, operators record release times in the launch log by hand. |

Not on this list, and handled differently: the OS-level runner service (`backend/scripts/candle12_v3_oos2_service/`). It is not fingerprinted, so it can be improved during the window. It must never modify fingerprinted files or the store schema.

## LCSC branch policy (`lcsc/fwd3`, commit f564688)

- **What it is:** the LCSC ETH forward contract refreeze (FWD-3), committed by session cryptoscreener-48 in a separate worktree (`D:/cryptoscreener-worktrees/lcsc-fwd3`), branched from `d2a3700`.
- **It does not touch the 37 OOS-2 fingerprinted files.** It changes the LCSC contract doc and lock, `docs/lcsc_contract_history/`, `docs/contract_policy/shared_module_reviews.jsonl`, and LCSC-only `.gitattributes` lines.
- **It still changes files in this checkout.** `D:\cryptoscreener` (branch `feature/grid-trading`) runs OOS-2 from its working tree. A merge rewrites tracked files there, including `.gitattributes`. A `.gitattributes` change can re-normalise the line endings of fingerprinted files on the next checkout, which is CODE_DRIFT.
- **Rules:**
  1. Do not merge `lcsc/fwd3` into the OOS-2 checkout while OOS-2 is running.
  2. LCSC runs only from its own worktree.
  3. Any merge happens in a separate checkout, or after the OOS-2 read (2027-10-16 or later). Before a merge lands where OOS-2 runs, `guard.fingerprint_status()` must still report `OK` on the merged tree, in a fresh worktree.
  4. A merge must never invalidate, restart or move the CANDLE12 OOS-2 window. If it would, the merge waits.
- **Shared modules** used by both contracts: `indicators.py`, `research/config.py`, `_candle_utils.py`, `swing_detector.py`, `_db.py`. They cannot change for either contract while OOS-2 runs.
