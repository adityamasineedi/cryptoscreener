-- Crypto Screener schema (TimescaleDB-ready; plain Postgres fallback via ensure_schema)

CREATE EXTENSION IF NOT EXISTS timescaledb;



CREATE TABLE IF NOT EXISTS symbols (

    symbol          TEXT PRIMARY KEY,

    base_asset      TEXT NOT NULL,

    quote_asset     TEXT NOT NULL,

    market_type     TEXT NOT NULL, -- spot | futures_perp

    exchange        TEXT NOT NULL DEFAULT 'binance',

    status          TEXT NOT NULL DEFAULT 'TRADING',

    contract_type   TEXT,

    price_precision INT,

    qty_precision   INT,

    listed_at       TIMESTAMPTZ,

    delisted_at     TIMESTAMPTZ,

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()

);



CREATE TABLE IF NOT EXISTS asset_metadata (

    asset               TEXT PRIMARY KEY,

    chain               TEXT,

    contract_address    TEXT,

    decimals            INT,

    provider_id         TEXT,

    symbols             JSONB NOT NULL DEFAULT '[]',

    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()

);



CREATE TABLE IF NOT EXISTS ohlcv (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    timeframe       TEXT NOT NULL,

    open            DOUBLE PRECISION NOT NULL,

    high            DOUBLE PRECISION NOT NULL,

    low             DOUBLE PRECISION NOT NULL,

    close           DOUBLE PRECISION NOT NULL,

    volume          DOUBLE PRECISION NOT NULL,

    quote_volume    DOUBLE PRECISION,

    trade_count     INT,

    taker_buy_base  DOUBLE PRECISION,

    taker_buy_quote DOUBLE PRECISION,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol, timeframe)

);



SELECT create_hypertable('ohlcv', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS funding_rates (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    funding_rate    DOUBLE PRECISION NOT NULL,

    mark_price      DOUBLE PRECISION,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol)

);



SELECT create_hypertable('funding_rates', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS open_interest (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    open_interest   DOUBLE PRECISION NOT NULL,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol)

);



SELECT create_hypertable('open_interest', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS liquidations (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    side            TEXT NOT NULL, -- BUY = short liq, SELL = long liq

    price           DOUBLE PRECISION NOT NULL,

    quantity        DOUBLE PRECISION NOT NULL,

    quote_qty       DOUBLE PRECISION,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol, side, price, quantity)

);



SELECT create_hypertable('liquidations', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS volume_history (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    timeframe       TEXT NOT NULL,

    volume          DOUBLE PRECISION NOT NULL,

    quote_volume    DOUBLE PRECISION,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol, timeframe)

);



SELECT create_hypertable('volume_history', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS price_snapshots (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    price           DOUBLE PRECISION NOT NULL,

    source          TEXT NOT NULL DEFAULT 'binance',

    PRIMARY KEY (time, symbol)

);



SELECT create_hypertable('price_snapshots', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS signals (

    id              BIGSERIAL PRIMARY KEY,

    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    symbol          TEXT NOT NULL,

    timeframe       TEXT NOT NULL,

    state           TEXT NOT NULL,

    reasons         JSONB NOT NULL DEFAULT '[]',

    scores          JSONB NOT NULL DEFAULT '{}',

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()

);



CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals (symbol, time DESC);

CREATE INDEX IF NOT EXISTS idx_symbols_market ON symbols (market_type, status);



CREATE TABLE IF NOT EXISTS structure_events (

    time            TIMESTAMPTZ NOT NULL,

    symbol          TEXT NOT NULL,

    timeframe       TEXT NOT NULL,

    event_type      TEXT NOT NULL,

    price           DOUBLE PRECISION,

    strength        DOUBLE PRECISION,

    evidence        JSONB NOT NULL DEFAULT '{}',

    source          TEXT NOT NULL DEFAULT 'market_structure',

    PRIMARY KEY (time, symbol, timeframe, event_type, price)

);



SELECT create_hypertable('structure_events', by_range('time'), if_not_exists => TRUE);



CREATE TABLE IF NOT EXISTS supply_demand_zones (

    id              BIGSERIAL PRIMARY KEY,

    symbol          TEXT NOT NULL,

    timeframe       TEXT NOT NULL,

    zone_type       TEXT NOT NULL,

    high            DOUBLE PRECISION NOT NULL,

    low             DOUBLE PRECISION NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL,

    strength        DOUBLE PRECISION,

    freshness       DOUBLE PRECISION,

    touch_count     INT NOT NULL DEFAULT 0,

    reaction_strength DOUBLE PRECISION,

    status          TEXT NOT NULL,

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()

);



CREATE INDEX IF NOT EXISTS idx_sd_zones_symbol ON supply_demand_zones (symbol, timeframe, status);

CREATE INDEX IF NOT EXISTS idx_volume_history_sym ON volume_history (symbol, timeframe, time DESC);

CREATE INDEX IF NOT EXISTS idx_price_snapshots_sym ON price_snapshots (symbol, time DESC);

CREATE TABLE IF NOT EXISTS setup_analyses (
    id              BIGSERIAL PRIMARY KEY,
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    status          TEXT NOT NULL,
    direction       TEXT,
    payload         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_setup_analyses_sym ON setup_analyses (symbol, time DESC);

CREATE TABLE IF NOT EXISTS setup_events (
    time            TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    price           DOUBLE PRECISION,
    state           TEXT,
    evidence        JSONB NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL DEFAULT 'setup_signal_engine',
    PRIMARY KEY (time, symbol, timeframe, event_type, price)
);

SELECT create_hypertable('setup_events', by_range('time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_setup_events_sym ON setup_events (symbol, timeframe, time DESC);

