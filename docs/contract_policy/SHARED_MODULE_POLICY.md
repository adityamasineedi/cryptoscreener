# Cross-contract shared-module policy

**Applies to:** every frozen research contract lock. Today that is `docs/lcsc_eth_forward_contract.lock.json` (LCSC ETH forward) and `docs/candle12_v3_oos_contract.lock.json` (CANDLE12 V3 OOS).
**Tool:** `backend/scripts/contract_policy/shared_modules.py`
**Ledger:** `docs/contract_policy/shared_module_reviews.jsonl` (append-only)

## Rule

A frozen lock is **never** updated to match changed code: not by a script, not by `--force`, not by hand. Every change to a fingerprinted file is handled as follows:

1. **Detect.** `shared_modules.py report` lists:
   - every fingerprinted file whose bytes differ from any lock;
   - every contract it affects (a shared file affects all the contracts that fingerprint it);
   - the classification and the decision per contract.

   Exit 0 only when nothing changed.
2. **Classify** each change, for exactly that frozen sha → current sha:
   - **`NO_BEHAVIOR_CHANGE`.** The reviewer certifies that no number, decision or record can differ: comments, formatting, logging, dead code. Recording it requires the reviewed diff committed in the repo and test evidence written as `command -> result`.
   - **`BEHAVIOR_CHANGE`.** Anything that can change a signal, fill, price, size, fee, funding, exception, record or read.
   - **`UNREVIEWED_CHANGE`.** No ledger entry, or the file changed again after its review. A review covers exactly the bytes it names.
3. **Decide**, per contract:
   - **All changes `NO_BEHAVIOR_CHANGE` → `ALLOW_DOCUMENTED_REFREEZE`.** The contract is refrozen with its own audited command (LCSC: `forward.py refreeze`; CANDLE12: its freeze for a new contract id). The refreeze cites the ledger entries. The old lock is kept as SUPERSEDED.
   - **Any `BEHAVIOR_CHANGE` or `UNREVIEWED_CHANGE` → `RESTART_REQUIRED`** for **every** affected contract. That means a new contract version whose window starts after the refreeze and a clean-checkout verification. Results from the old window are not carried over.

   Before a contract's window has started, a restart keeps the planned start, because no window data exists yet.
4. **Never silently.** Each contract's own guard already refuses to run on any drift (LCSC exit 3; CANDLE12 `CONFIG_DRIFT` / `CODE_DRIFT`). This policy only decides what may follow, and always through an explicit, logged command.

## Shared files (both contracts, as of 2026-10-15)

- `backend/app/engines/mtf/indicators.py`
- `backend/app/research/config.py`
- `backend/app/signals/_candle_utils.py`
- `backend/app/signals/swing_detector.py`
- `backend/scripts/strategy_rank/multicap/_db.py`

Editing any of them for unrelated work restarts **both** forward windows unless it is reviewed `NO_BEHAVIOR_CHANGE` before either window opens.

**Practical rule:** make unrelated changes in new modules or on another branch, never in place in these files.
