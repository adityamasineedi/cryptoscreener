# Crypto Screener

Production-oriented real-time crypto market screener.

**Hard rule:** when `USE_REAL_DATA=true`, the system never fabricates market data.

## Architecture (Phase 1–2)

```
Binance Futures WS (!ticker@arr, !markPrice@arr@1s)
        ↓
WebSocket Manager (reconnect, 24h refresh, multiplexed)
        ↓
Normalizer
        ↓
Market Store (+ optional Redis)
        ↓
FastAPI REST + /ws/market
        ↓
React (virtualized table, incremental row updates)
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for full plan.

## Quick start (local, no Docker)

Redis/Postgres optional — backend falls back to in-memory store.

```bash
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy ..\.env.example ..\.env   # if needed
# Ensure REDIS_ENABLED=false DATABASE_ENABLED=false for local without Docker
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

## API

- `GET /api/health`
- `GET /api/symbols`
- `GET /api/screener/futures`
- `GET /api/coin/{symbol}`
- `WS /ws/market`
- `WS /ws/screener`
- `WS /ws/coin/{symbol}`

## Tests

```bash
cd backend
pytest -q
```
