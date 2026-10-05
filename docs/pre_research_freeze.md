# Pre-research code freeze

**Tag:** `pre-research-freeze-20261005`  
**Status:** Working baseline before new strategy research  
**Frozen:** 2026-10-05  
**Parent strategy freeze:** [`v1_freeze.md`](./v1_freeze.md) (`v1-combo02-long-htf`) — HTF entry logic unchanged

## Why this freeze

Snapshot the current app (paper sizing, extended paper universe, chart/UI fixes) so upcoming research work can proceed without silently mutating the live/paper baseline.

## What is included

| Area | Notes |
|------|--------|
| Paper sizing | `paper_sizing.py` — tick/lot rounding, leverage cap, fees closer to Binance USDT-M |
| Paper risk caps | Default max open positions **15**, max open risk **30%** |
| V1 paper books | Frozen claim set remains BTC/ETH/SOL; **extended liquid majors** on COMBO_02 1h paper @ **2%** (outside original freeze evidence) |
| Chart / UI | Time-axis + annotation fixes; Backtest / Paper / V1 watcher panel sync |
| Discovery | Symbol-discovery + persistence updates supporting the larger watch set |
| Tests | Paper sizing, paper trade, v1 production, chart, backtest UI |

## Do not change under this tag (without a new freeze)

1. Do **not** weaken COMBO_02 HTF gate / Path A identity (`v1-combo02-long-htf` rules still apply).
2. Do **not** treat extended paper majors as freeze-evidence without a new validation window.
3. Do **not** silently change paper sizing / fee / risk-cap defaults while labeling runs as this baseline.
4. Research experiments must use **new combo IDs / research runners / research tables** — not mutate Path A entry logic in place.

## Allowed next (research track)

- 1D / broad-universe strategy research using existing `research_*` persistence and `postgres_ohlcv`
- New combo / candidate pipelines labeled research-only
- Deeper backfill and coverage work
- Intraday validation on deep symbols (BTC/ETH/SOL) once history allows

## Restore this baseline

```bash
git checkout pre-research-freeze-20261005
# or
git switch -c restore/pre-research pre-research-freeze-20261005
```

## Related

- Strategy logic freeze: [`v1_freeze.md`](./v1_freeze.md)
- Production profile module: `backend/app/research/v1_production.py`
