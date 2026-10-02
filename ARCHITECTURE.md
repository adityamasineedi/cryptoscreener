# Crypto Screener — Architecture

## 1. Product scope

Real-time crypto screener (fundamentals, futures, structure, S/D, liquidations, volume, OI, MTF TA, filters, coin detail, charts).

**Phases 1–21 implemented** in this tree. Screener does **not** place trades.

## 2. Folder structure

```
cryptoscreener/
  config/                 # market, indicators, screener, providers YAML
  backend/
    app/
      api/                # FastAPI REST + WS
      core/               # logging, RateLimiter, cache, audit, health
      ingestion/          # Binance REST/WS, klines, OI, liquidations, providers
      engines/            # MTF, structure, S/D, volume, OI, liq, filters, orchestrator
      models/             # Pydantic schemas + FreshValue + Candle
      services/           # market/ohlcv/engine stores, screener, system stats
      db/init.sql         # Timescale schema
    tests/
  frontend/
    src/                  # React + Zustand + TanStack Virtual + Lightweight Charts
  docker-compose.yml
```

## 3. Data flow

```
Exchange (Binance)
  → WebSocket Manager (ticker/mark all-market arrays)
  → KlineWebSocketManager (sharded kline streams, MAX 900/conn)
  → Liquidation stream (!forceOrder@arr)
  → OIScheduler (staggered REST openInterest)
  → Market / OHLCV / Engine stores
  → CalculationOrchestrator (MTF, structure, S/D, volume)
  → Providers (CoinGecko, DefiLlama) with rate limits
  → FastAPI /ws/screener (row_patch) + REST
  → React virtualized table (row-level patches)
```

## 4. Kline WebSocket sharding

`streams = symbols × timeframes` (e.g. 527 × 6 = 3162)

`connections = ceil(streams / MAX_STREAMS_PER_CONNECTION)` with `MAX_STREAMS_PER_CONNECTION=900`

Manager dynamically: discover symbols → generate streams → shard → connect → reconnect → rebalance → 23h refresh → ping/pong → malformed drop → no duplicate streams.

## 5. Freshness model

Every value is `FreshValue{value, timestamp, source, status}`:

`LIVE → CACHED → STALE → UNAVAILABLE` / `WAITING`

UI shows **"Data unavailable"** or **"Waiting for live data..."** — never fake numbers.

## 6. Key endpoints

- `GET /api/health`
- `GET /api/system/stats`
- `GET /api/health/providers`
- `GET /api/screener/futures`
- `POST /api/screener/filter`
- `GET /api/charts/{symbol}/ohlcv|oi|liquidations`
- `WS /ws/market`, `/ws/screener` (row_patch), `/ws/coin/{symbol}`

## 7. Security

- No API keys in frontend
- Public market data for screener
- Screener does **not** place trades
