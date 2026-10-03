# Changelog

## v1-combo02-long-htf — 2026-10-03

**Freeze tag:** `v1-combo02-long-htf`  
**Doc:** [`docs/v1_freeze.md`](./docs/v1_freeze.md)

Production-candidate freeze for the HTF-gated **COMBO_02 LONG** playbook (BTC/ETH/SOL, 1h):

- Research/backtest: `COMBO_02` with `require_htf_alignment=True` (fail closed).
- Paper Path A: always requires 4h+1h bullish HTF; `LONG_ENTRY_CANDIDATE` no longer bypasses the gate in `path_a` mode.
- `ResearchService.walk_forward` loads 1h/4h for HTF-gated combos.
- `COMBO_02_LOCAL` marked LEGACY/RESEARCH-ONLY (not v1).
- Path B labeled experimental — **not** COMBO_02 v1.
- v1-critical tests: `test_combo02_htf_gate.py` + Path A HTF tests in `test_paper_trade.py`.

Any behavior-changing edit to frozen v1 logic requires a new version tag (v2+), not silent mutation of this freeze.
