# Execution adapter: mock exchange, then Binance Futures testnet (owner rulings D128c, D131)

Proposal on branch `live/d128c-exec-adapter-mock`, stacked on `live/d128d-kill-switches`. **CONSTITUTION v2.5 C1:** no real orders and no real money. Exchange keys are allowed only for the Binance Futures **testnet**. The owner sets them in the root `.env`; they are never logged, printed or committed. They are used only for this adapter, the kill switches and the reconciliation tests. Real-money keys or orders need C1 v3.

**Nothing is wired into live paper.** `app/execution/` is a library. No live module imports it, and a test pins that. Connecting it to a book is a later, separate owner decision.

## Parts (`backend/app/execution/`)

| File | What |
|---|---|
| `models.py` | `OrderRequest`, `Order`, `Fill`, `Position` (signed qty), `Bracket` (Binance one-way mode vocabulary) |
| `ids.py` | `client_order_id(strategy, signal_key, leg, version)`: deterministic `cs-<leg>-<24 hex>` (≤ 36 chars, Binance pattern). Legs EN / SL / TP / XT / FL; version > 1 for a cancel-and-replace |
| `interface.py` | `ExchangeClient` (place / cancel / amend / get / open orders / positions) and the error types |
| `mock_exchange.py` | `MockBinanceFutures`: deterministic fake with MARKET and LIMIT fills, mark-triggered STOP_MARKET / TAKE_PROFIT_MARKET, reduce-only rules, duplicate-id refusal, partial fills, injected timeouts (before or after the order is accepted). **The default everywhere.** |
| `adapter.py` | `ExecutionAdapter` and `ReconciliationLoop` (described below) |
| `testnet_client.py` | `BinanceFuturesTestnetClient`, HMAC-SHA256 signed REST. It refuses to construct unless every guard passes. |

`ExecutionAdapter`:
- `open_bracket`: entry, then reduce-only exchange-side STOP_MARKET and TAKE_PROFIT_MARKET for exactly the filled quantity.
- `sync_protection`: resizes the protection on partial fills by cancel-and-replace, and cancels the other leg when one exit fills (OCO).
- `amend_stop`: cancel-and-replace. `amend_entry`: LIMIT modified in place. Also `cancel_entry` and `close_bracket` (reduce-only MARKET).
- `place`: idempotent retries. A timeout retries the same id; a duplicate id fetches the original.
- `sync_positions` and `reconcile(ledger)`: exchange qty vs ledger qty, and a stop must cover every position. A mismatch calls `mismatch_hook`.

`ReconciliationLoop`:
- Every 60 s: `poll()` then `reconcile(ledger_fn())`.
- The hook is `KillSwitch.on_position_mismatch` (D128d): trip `mismatch`, flatten paper, block entries.

## Testnet guards (each raises `TestnetRefused`)

1. `EXECUTION_TESTNET_ENABLED=true`. Default false; only the boolean true counts.
2. The constitution's first line says v2.5 or later. The file is `EXECUTION_CONSTITUTION_PATH`, default `D:/cryptoscreener-worktrees/research-audit/prompts/CONSTITUTION.md`.
3. The base URL is `https://` on an allow-listed testnet host: `testnet.binancefuture.com` or `demo-fapi.binance.com`. No credentials and no other port in the URL. Everything else is refused, including `fapi.binance.com`. It is re-checked on every request.
4. A key and a secret are present. They are held privately; `repr` shows `key=***`. The settings fields are `SecretStr`, so a settings dump never shows them.

## Testnet `.env` variable names (the owner sets the values; I never create, request or read them)

```
EXECUTION_TESTNET_ENABLED=true
BINANCE_TESTNET_FUTURES_REST=https://testnet.binancefuture.com
BINANCE_TESTNET_API_KEY=<owner>
BINANCE_TESTNET_API_SECRET=<owner>
EXECUTION_CONSTITUTION_PATH=D:/cryptoscreener-worktrees/research-audit/prompts/CONSTITUTION.md
```
For the opt-in integration test only, in the shell environment and never in CI: `RUN_TESTNET_INTEGRATION=1`, plus the same names exported in that shell (the test reads `os.environ`, not `.env`).

## Tests

- `tests/test_execution_adapter.py` (21, mock only):
  - ids;
  - bracket protection; stop triggers with TP cancelled; TP triggers with stop cancelled; short side;
  - lost acknowledgement creates no duplicate; a timeout before the order is seen is retried; retries give up placing nothing; a restart re-running a signal creates nothing; duplicate id refused;
  - partial fills resize the protection by cancel-and-replace; a stop that triggers after a manual close expires (no accidental short);
  - amend stop, amend after the stop filled, amend a LIMIT entry in place;
  - reconcile OK; qty mismatch fires the hook; a missing stop is a mismatch;
  - **a mismatch trips the real D128d kill switch and flattens the paper book**;
  - the loop runs on its interval (default 60 s);
  - live code does not import the package.
- `tests/test_execution_testnet_guard.py` (21): refuses by default (flag off); refuses a constitution older than v2.5, missing or unreadable; refuses 10 non-testnet URLs (mainnet, http, look-alike hosts, credentials, wrong port); refuses missing keys; builds for the testnet hosts with keys hidden; Binance HMAC signature; a tampered base URL sends nothing; settings keys are secret strings. No network.
- `tests/test_execution_testnet_integration.py` (1, **skipped** unless `RUN_TESTNET_INTEGRATION=1`, the flag and keys are in the environment and `CI` is unset): one tiny BTCUSDT bracket on the testnet. It checks the protection and idempotency, then closes the bracket.
