# Feature Matrix — Reference Screenshots vs Current Implementation

> **Note:** No screenshot image files were found in the workspace. This matrix is built from the reference domains you described (Fundamentals / On-chain, Technical / Structure, Futures / Derivatives) and a full scan of the repository after the audit implementation pass.

---

## 1. Screenshot feature matrix (reference IA)

### Top-level domains
| Domain | In screenshots |
|--------|----------------|
| Fundamentals | Yes |
| Futures | Yes |
| Market Structure | Yes |
| Supply/Demand | Yes |
| Liquidations | Yes |
| Volume Analysis | Yes |
| Open Interest | Yes |

### Coin detail tabs
| Tab | In screenshots |
|-----|----------------|
| Overview | Yes |
| Performance | Yes |
| Technicals | Yes |
| Valuation | Yes |
| Derivatives | Yes |
| Addresses | Yes |
| Transactions | Yes |
| Sentiment | Yes |

### Fundamentals / on-chain columns
Rank, Price, 24h Change, Market Cap, FDV, 24h Volume, Volume Change, Vol/MCap, Circulating/Total/Max Supply, Category, TVL, Social Dominance, Transaction Volume (+ change), Performance %, Volatility, NVT, Velocity, Williams %R

### Presets
Large Cap / Mid Cap / Small Cap / Custom (filter configs, not hardcoded lists)

### Addresses / decentralization
Holder count, growth, concentration (top 10/20/50/100 %), largest holder %, exchange-held supply

### Transactions
Tx count, volume, change, avg value, large-tx activity

### Sentiment
Social dominance/volume, mentions, engagement, sentiment ± change

### Technicals + entry/exit
RSI, EMA, SMA, ATR, VWAP, Williams %R, volatility, RVOL, structure, BOS, CHOCH; explainable Entry/Exit; multi-component Tech Rating (not black-box Strong Buy)

---

## 2. Current implementation matrix

| Feature | Status | Real data? | Notes |
|---------|--------|------------|-------|
| Rank | **Done** | Yes | Assigned by sort order in screener page |
| Price / 24h % / Volume | **Done** | Binance ticker | FreshValue + methodology |
| Market Cap / FDV / Supply | **Done** | CoinGecko | WAITING/UNAVAILABLE when limited |
| TVL / Category | Partial | DefiLlama / CoinGecko | Category often UNAVAILABLE |
| Vol/MCap | **Done** | Derived | `quote_volume_24h / market_cap` |
| Volume Change | **Done** (honest) | OHLCV windows | 1h/4h/24h/7d; WAITING without two windows |
| Social Dominance | Wired WAITING | Needs provider | SentimentProvider |
| Tx Volume / change | Wired WAITING | Needs chain+mapping | OnChainProvider + assets.yaml |
| Performance % | **Done** (honest) | 1d OHLCV | 1D–YTD; WAITING if history missing (never uses 24h ticker) |
| Volatility | **Done** | MTF engine | On closed candles |
| NVT / Velocity | **Done** (honest) | On-chain or WAITING | True NVT WAITING; proxies under EXPERIMENTAL only |
| market_rank vs screener_rank | **Done** | CoinGecko / table | Separated; not mixed |
| Williams %R | **Done** | OHLCV | Formula tested |
| Cap presets | **Done** | Filter YAML | large/mid/small/custom — not coin lists |
| Overview tab | **Done** | Mixed live | Entry/Exit + Tech Rating visible |
| Performance tab | **Done** | Partial live | 24h + vol metrics; series note |
| Technicals tab | **Done** | Indicators + conditions | Explainable |
| Valuation tab | **Done** | Derived + methodology | |
| Derivatives tab | **Done** | OI/funding/liq | |
| Addresses tab | Wired WAITING | Provider stub | No decentralization claim |
| Transactions tab | Wired WAITING | Provider stub | |
| Sentiment tab | Wired WAITING | Provider stub | Never fabricated |
| RSI/EMA/SMA/ATR/VWAP | **Done** | OHLCV | Config-driven |
| RVOL | **Done** | Volume engine | |
| Structure / BOS / CHOCH | **Done** | Structure engine | |
| S/D zones | **Done** | S/D engine | |
| OI + changes | **Done** | Binance REST scheduler | Progressive coverage |
| Liquidations | Partial | forceOrder | Often WAITING on this network |
| Entry/Exit engine | **Done** | Local engines | Explicit conditions; no black-box |
| Explainable Tech Rating | **Done** | Weighted components | BULLISH_BIAS/NEUTRAL/BEARISH_BIAS only |
| Top-level domain screens | **Done** | Domain column sets | Same live universe, domain-focused columns |
| Metric methodology field | **Done** | FreshValue.methodology | |

---

## 3. Missing features (honest gaps)

### Still WAITING — need external providers/keys + mappings
| Feature | Required source |
|---------|-----------------|
| Social dominance / sentiment / mentions / engagement | Santiment, LunarCrush, or similar via `providers.yaml` + `SENTIMENT_API_KEY` |
| Holder concentration / growth / exchange-held | Indexer via `OnChainProvider` + `ONCHAIN_API_KEY` + `config/assets.yaml` mapping |
| On-chain tx count/volume/avg/large-tx | Same on-chain provider + asset chain/contract mapping |
| True NVT / Velocity | On-chain transaction volume (never trading volume) |
| Multi-horizon Performance % | Needs enough 1d OHLCV history (in-memory and/or `DATABASE_ENABLED`) |
| Volume Change % | Needs two full OHLCV windows per horizon |

### Not claimed complete
- Liquidation stream may stay WAITING depending on network/WS availability
- Category/TVL completeness depends on CoinGecko/DefiLlama coverage and rate limits

---

## 4. Data source required for each missing feature

| Missing metric | Source | Methodology note |
|----------------|--------|------------------|
| Vol/MCap | Binance quote vol + CoinGecko mcap | `quote_volume_24h / market_cap` — **implemented** |
| Volume Change | OHLCV or successive ticker snapshots | `(vol_now - vol_prev) / vol_prev` — field WAITING |
| MCap/FDV | CoinGecko | `market_cap / fdv` — **implemented** |
| Circ/Max ratio | CoinGecko | `circulating / max` — in valuation engine |
| NVT | mcap / on-chain tx volume | WAITING until on-chain provider |
| Velocity | on-chain tx vol / mcap | WAITING without on-chain |
| Williams %R | OHLCV | `(HHn - C) / (HHn - LLn) * -100` — **implemented** |
| Social metrics | Santiment/LunarCrush | Provider adapter — WAITING |
| Holders | On-chain indexer | Provider adapter — WAITING |
| Tx metrics | On-chain indexer | Provider adapter — WAITING |
| Entry/Exit | Local engines | Explicit boolean conditions — **implemented** |
| Tech Rating | Local engines | Weighted components — **implemented** |

---

## 5. Implementation plan (this pass) — status

1. Document matrix — **done**
2. Extend metric model + valuation engine + Williams %R — **done** + tested
3. Wire presets from `config/screener.yaml` — **done** + tested
4. Holder/tx/sentiment provider interfaces returning WAITING — **done** + tested
5. Entry/Exit + Tech Rating engines (explainable) — **done** + tested
6. TopNav domains + CoinDetail tabs + presets UI — **done**
7. Tests for formulas, presets, rating explainability — **done** (64 pytest passing)
8. On-chain/sentiment **not** marked complete without real providers

---

## 6. API surface added

| Endpoint | Purpose |
|----------|---------|
| `GET /api/screener/presets` | List filter-config presets |
| `GET /api/screener/futures?preset=` | Apply preset filters |
| `GET /api/screener/fundamentals?preset=` | Fundamentals sort + preset |
| `GET /api/coin/{symbol}` | Includes analytical `tabs` + `signals` |
| `GET /api/coin/{symbol}/tabs` | Explicit tab payload |
