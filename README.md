# Crypto Screener

Production-oriented real-time crypto market screener.

**Hard rule:** when `USE_REAL_DATA=true`, the system never fabricates market data.

**COMBO_02 v1 freeze:** HTF-gated LONG playbook — see [`docs/v1_freeze.md`](./docs/v1_freeze.md) and tag `v1-combo02-long-htf` ([`CHANGELOG.md`](./CHANGELOG.md)).

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

## Paper trade bot

The virtual paper bot runs **inside the backend** (no separate process). It auto-opens simulated longs on setup signals and exits at stop / TP1. No real exchange orders.

### 1. Configure `.env`

```bash
cp .env.example .env
```

Minimum flags for the bot:

```env
USE_REAL_DATA=true
PAPER_TRADE_ENABLED=true
PAPER_STARTING_EQUITY=1000
PAPER_ENTRY_MODE=path_a
# path_a = Trend+BOS (research default)
# path_b = full LONG_ENTRY_CANDIDATE only
```

Optional risk gates (defaults are already set in `.env.example`):

```env
PAPER_RISK_GATES_ENABLED=true
PAPER_ALLOWED_GROUPS=BTC,ETH,large-cap,mid-cap
PAPER_MAX_OPEN_POSITIONS=5
```

For durable book / OHLCV across restarts, enable Redis + Postgres (Docker Compose does this automatically). For a quick local run without Docker, keep `REDIS_ENABLED=false` and `DATABASE_ENABLED=false` — the bot still works in-memory.

### 2. Start the stack

**Option A — Docker (recommended for the bot):**

```bash
docker compose up --build
```

**Option B — local (two terminals):**

```bash
# Terminal 1 — backend
cd backend
python -m venv .venv
.\.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — frontend
cd frontend
npm install
npm run dev
```

### 3. Turn Auto on

1. Open http://localhost:5173/paper
2. Confirm **Auto ON** (green). If it shows Auto OFF, click to enable.
3. Watch **READY** opportunities — when Auto is on, Path A opens a virtual long; exits mark/last at stop or TP1.
4. Use **Reset** only if you want to wipe the paper book back to starting equity.

### 4. Check status (optional)

```bash
curl http://localhost:8000/api/paper/status
curl http://localhost:8000/api/paper/positions
curl http://localhost:8000/api/paper/opportunities?limit=40
```

| Endpoint | Purpose |
|---|---|
| `GET /api/paper/status` | Equity, enabled flag, entry mode |
| `GET /api/paper/positions` | Open + closed virtual trades |
| `GET /api/paper/opportunities` | READY / NEAR / FORMING setups |
| `POST /api/paper/enable` | Auto ON |
| `POST /api/paper/disable` | Auto OFF |
| `POST /api/paper/reset` | Clear book + equity |

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Compose starts Redis, TimescaleDB, backend (`:8000`), and frontend (`:5173`). Backend env overrides enable Redis + Postgres inside the containers.

## Run non-stop (background)

Use Docker detached mode so the stack keeps running after you close the terminal. Services use `restart: unless-stopped` (come back after crashes / Docker Desktop restarts).

```bash
# From repo root
cp .env.example .env   # once, if missing

# Build and run in background
docker compose up -d --build

# Confirm all services are up
docker compose ps
curl http://localhost:8000/api/health

# Paper bot UI
# http://localhost:5173/paper  → Auto ON
```

Useful commands:

```bash
docker compose logs -f backend   # follow backend logs
docker compose logs -f           # all services
docker compose restart backend   # restart bot/API only
docker compose down              # stop everything (data volumes kept)
docker compose down -v           # stop + wipe Redis/Postgres volumes
```

Do **not** use `uvicorn --reload` for non-stop — that is for local coding only and dies when the terminal closes. Docker `-d` is the supported always-on path.

## API

- `GET /api/health`
- `GET /api/symbols`
- `GET /api/screener/futures`
- `GET /api/coin/{symbol}`
- `GET /api/paper/status` · `GET /api/paper/positions` · `GET /api/paper/opportunities`
- `POST /api/paper/enable` · `POST /api/paper/disable` · `POST /api/paper/reset`
- `WS /ws/market`
- `WS /ws/screener`
- `WS /ws/coin/{symbol}`

## Tests

```bash
cd backend
pytest -q
```
