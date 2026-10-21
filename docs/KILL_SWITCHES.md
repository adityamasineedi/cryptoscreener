# Account kill switches for live paper (owner ruling D128d)

Proposal on branch `live/d128d-kill-switches`. It sits behind `KILL_SWITCHES_ENABLED`, which defaults to **false**. Merged with the flag off, live does what it does today:
- the engine's `kill_switch` stays `None`, no opener calls into the new module, and no monitor task starts;
- the new endpoints answer 409.

The replay test proves it: `tests/test_kill_switch_flag_off_replay.py` compares against a decision journal taken on b009aca, byte for byte. Paper only (C1 v2.5).

## Rules (when the flag is on)

| Rule | Setting (root `.env`) | Default when on | Effect |
|---|---|---|---|
| Account daily loss limit | `KILL_SWITCH_DAILY_LOSS_LIMIT_PCT` | 0.05 | Day P&L = closes stamped on the current UTC day plus the open positions' unrealized P&L, across every book. At or below −limit × start-of-day equity, new entries are refused, latched until the next 00:00 UTC. |
| Max open risk | `KILL_SWITCH_MAX_OPEN_RISK_PCT` | 0.10 | Open risk of every book plus the new trade's risk must stay ≤ limit × equity. |
| Max position size | `KILL_SWITCH_MAX_POSITION_NOTIONAL_USD`, `KILL_SWITCH_MAX_POSITION_NOTIONAL_PCT` | 0 (off), 2.0 | The new position's notional must be ≤ the USD cap and ≤ pct × equity. |
| Manual flatten all | API / CLI below | n/a | Closes every open paper position at the current price, then refuses entries until the owner clears `manual_flatten`. |
| Heartbeat auto-flatten | `KILL_SWITCH_HEARTBEAT_TIMEOUT_MINUTES`, `KILL_SWITCH_AUTO_FLATTEN_ON_HEARTBEAT` | 90, true | Heartbeat = the newest closed-bar evaluation (the passed-book watcher's last evaluated closed 1h bar). If it is older than the timeout, the switch trips `heartbeat`, flattens once and refuses entries until cleared. Checked every 60 s. |
| Mismatch auto-flatten | `KILL_SWITCH_AUTO_FLATTEN_ON_MISMATCH` | true | `KillSwitch.on_position_mismatch(details)` is the hook the D128c reconciliation loop calls when an exchange position and the ledger disagree. It trips `mismatch`, flattens once and refuses entries until cleared. |

**Where the checks sit.**
- Every paper opener (legacy, COMBO_02 v1, experimental V2, V2.1-A, passed book) checks the kill switch right after the existing second risk gate, and never in replay mode.
- A refusal is the engine's normal skip, `<SYM>:kill_switch:<rule>...`. The watchers journal it like any other skip.
- The existing per-book stops (D4/D27) are unchanged.

**Flatten.**
- Each position is closed through the normal close path (fees, `paper_trades` row, stop counters, alert) at the live mark, else its last mark, else its entry.
- It is booked with `exit_reason = KILL_SWITCH_FLATTEN`, and `signal_snippet.kill_switch = {reason, by, price_source}`.
- It covers the paper ledger engine only. The book-16 and CANDLE12_V2 virtual stores and the forward runners (FWD-1, FWD-3, OOS-2) are frozen forward contracts with no exposure, and they are not touched.

**Journal.** Every trip, clear, refused entry and flatten is appended to `data/paper/kill_switch_journal.jsonl` (write-only).

**Heartbeat limits.**
- It is an in-process check: it catches a stalled feed or a hung loop, not a dead process.
- For a dead process, the protection is the exchange-side reduce-only stops (D128c) and the external monitor (VPS plan, section 5).
- With no heartbeat source (the passed watcher off), the heartbeat rule is off.

## Owner commands

Turn on (D53, owner only; `.env` changes need a restart):
```
KILL_SWITCHES_ENABLED=true
KILL_SWITCH_DAILY_LOSS_LIMIT_PCT=0.05
KILL_SWITCH_MAX_OPEN_RISK_PCT=0.10
KILL_SWITCH_MAX_POSITION_NOTIONAL_PCT=2.0
KILL_SWITCH_HEARTBEAT_TIMEOUT_MINUTES=90
```
Check the limits against the books before turning it on:
- V1 risks 2% per trade and the passed book 1–2%. A 10% open-risk cap is about 5 full-size trades.
- The current policy cap `PAPER_MAX_OPEN_RISK_PCT` is 0.30.
- With the flag on, entries the old rules allowed may be refused. That is a live behaviour change (C2), owned by the owner.

```powershell
cd D:\cryptoscreener
backend\.venv\Scripts\python.exe backend\scripts\ops\flatten_all.py status
backend\.venv\Scripts\python.exe backend\scripts\ops\flatten_all.py flatten --note "why"     # type FLATTEN ALL
backend\.venv\Scripts\python.exe backend\scripts\ops\flatten_all.py clear manual_flatten     # type CLEAR
```
API (loopback, or `X-Paper-Admin` with `PAPER_ADMIN_TOKEN`):
- `GET /api/paper/kill-switch`
- `POST /api/paper/kill-switch/flatten-all?confirm=FLATTEN%20ALL&note=...`
- `POST /api/paper/kill-switch/clear?trip=<manual_flatten|heartbeat|mismatch|daily_loss_limit>&confirm=CLEAR`

A flatten changes the forward record. Log it in DECISIONS with the reason.

## Tests

- `tests/test_kill_switches.py`: 18 golden tests.
  - off by default;
  - daily loss: realized, unrealized, earlier days ignored, UTC-day reset;
  - max open risk across books;
  - max notional, USD and pct;
  - manual flatten: prices, ledger queue, snippet, block and clear, journal;
  - flatten price fallbacks;
  - heartbeat: stale, flatten once, latch; newest source wins; a broken source is ignored; no source means no trip; auto-flatten off;
  - mismatch hook: flatten once, latch;
  - API: 409 when off, typed confirmation, writer lock, remote peer 403;
  - CLI: nothing is sent without confirmation.
- `tests/test_kill_switch_flag_off_replay.py`: 4 tests. Flag off, a disabled object, and on-with-unreached-limits are each byte-identical to the b009aca journal (fixture sha256 `fa7045f3…`). A guard checks the scenario really opens, exits and refuses.
