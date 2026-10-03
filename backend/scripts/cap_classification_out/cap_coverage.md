# Historical Market-Cap Classification Coverage

**Status:** `CAP_DATA_READY_FOR_FULL_RESEARCH`

## Source
- Provider: `coingecko`
- Endpoint: `/coins/{id}/market_chart`
- Units: USD market capitalization (CoinGecko market_caps)
- Currency: `usd`
- Rule version: `research_hist_mcap_v1`
- Timestamp semantics: effective_time = CoinGecko chart point time (UTC). cap_group_at(T) uses latest observation with effective_time <= T. Never uses future observations.
- Missing data: CAP_GROUP_UNAVAILABLE — no fabrication / no silent substitute
- Fallback: None — no price/volume/rank/FDV/name inference
- BTC/ETH: classify_asset_group still labels BTC/ETH as BTC/ETH; they do not map to LARGE/MID/SMALL strategy groups.

### Thresholds (unchanged)
- LARGE >= 10,000,000,000
- MID [1,000,000,000, 10,000,000,000)
- SMALL [50,000,000, 1,000,000,000)

## Coverage
- Total Binance USDT-perp symbols: **528**
- With historical cap observations: **66**
- With no cap: **462**
- Partial history (<30d span): **0**

### Historical span buckets
- >=1m: 66
- >=3m: 66
- >=6m: 65
- >=1y: 0
- >=2y: 0
- >=3y: 0
- >=5y: 0

### Cap-group counts (as-of)
- LARGE_CAP: 7
- MID_CAP: 5
- SMALL_CAP: 37
- UNAVAILABLE: 477
- BTC: 1
- ETH: 1
- UNKNOWN: 0

## Blockers
- 462/528 USDT-perp symbols still have no historical market-cap observations (continue CoinGecko ingest)
- CoinGecko demo/free market_chart span is ~365d; >=1y/2y/3y/5y buckets remain empty without a paid plan or longer persisted history
- BTC/ETH taxonomy labels never map to LARGE/MID/SMALL strategy groups (unchanged classify_asset_group behavior)
