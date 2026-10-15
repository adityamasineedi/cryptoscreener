-- Optional persistence for INTRADAY_BOS_SD_V1.
-- The runner also writes backend/reports/intraday_bos_sd/state.json.
-- Apply with the project's normal migration path, or let the API keep the JSON state.

CREATE TABLE IF NOT EXISTS intraday_bos_sd_runs (
    spec_hash           TEXT PRIMARY KEY,
    strategy_version    TEXT NOT NULL,
    status              TEXT NOT NULL,
    report              JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS intraday_bos_sd_signals (
    idempotency_key     TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    setup_time          TIMESTAMPTZ NOT NULL,
    publication         TEXT NOT NULL,
    payload             JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
