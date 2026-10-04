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

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    symbol                  TEXT NOT NULL,
    provider                TEXT NOT NULL,
    provider_symbol         TEXT,
    observed_at             TIMESTAMPTZ NOT NULL,
    provider_generated_at   TIMESTAMPTZ,
    social_dominance        DOUBLE PRECISION,
    social_volume           DOUBLE PRECISION,
    mentions                DOUBLE PRECISION,
    engagement              DOUBLE PRECISION,
    sentiment               DOUBLE PRECISION,
    sentiment_change        DOUBLE PRECISION,
    raw_payload_hash        TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_obs ON sentiment_snapshots (symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_provider_obs ON sentiment_snapshots (provider, observed_at DESC);

-- Research historical data pipeline (additive; never truncates production OHLCV)
CREATE TABLE IF NOT EXISTS research_data_manifest (
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    requested_start     TEXT NOT NULL,
    requested_end       TEXT NOT NULL,
    actual_first        TEXT,
    actual_last         TEXT,
    candles_downloaded  INT NOT NULL DEFAULT 0,
    chunks_completed    INT NOT NULL DEFAULT 0,
    last_successful_chunk INT,
    last_successful_chunk_end_ms BIGINT,
    status              TEXT NOT NULL DEFAULT 'PENDING',
    last_error          TEXT,
    quality             TEXT,
    gap_count           INT NOT NULL DEFAULT 0,
    duplicate_count     INT NOT NULL DEFAULT 0,
    invalid_rows        INT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS research_dataset_versions (
    dataset_version     TEXT PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pipeline_version    TEXT NOT NULL,
    feature_version     TEXT,
    data_fingerprint    TEXT,
    git_commit          TEXT,
    symbols             JSONB NOT NULL DEFAULT '[]',
    timeframes          JSONB NOT NULL DEFAULT '[]',
    period_start        TEXT,
    period_end          TEXT,
    candle_counts       JSONB NOT NULL DEFAULT '{}',
    quality_report      JSONB NOT NULL DEFAULT '{}',
    payload             JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS research_pipeline_runs (
    run_id              TEXT PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    git_commit          TEXT,
    pipeline_version    TEXT NOT NULL,
    feature_version     TEXT NOT NULL,
    data_version        TEXT,
    configuration_hash  TEXT NOT NULL,
    configuration       JSONB NOT NULL DEFAULT '{}',
    symbols             JSONB NOT NULL DEFAULT '[]',
    timeframes          JSONB NOT NULL DEFAULT '[]',
    period_start        TEXT,
    period_end          TEXT,
    status              TEXT NOT NULL DEFAULT 'RUNNING',
    metrics             JSONB NOT NULL DEFAULT '{}',
    quality_report      JSONB NOT NULL DEFAULT '{}',
    failed_symbols      JSONB NOT NULL DEFAULT '[]',
    finished_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS research_feature_cache (
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    feature_version     TEXT NOT NULL,
    data_fingerprint    TEXT NOT NULL,
    first_time          TIMESTAMPTZ,
    last_time           TIMESTAMPTZ,
    bar_count           INT NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'COMPLETE',
    payload             JSONB NOT NULL DEFAULT '{}',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, feature_version, data_fingerprint)
);

CREATE TABLE IF NOT EXISTS research_bos_events (
    id                  BIGSERIAL PRIMARY KEY,
    dataset_version     TEXT NOT NULL,
    feature_version     TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    timeframe           TEXT NOT NULL,
    direction           TEXT NOT NULL,
    swing_price         DOUBLE PRECISION,
    bos_price           DOUBLE PRECISION,
    bos_distance        DOUBLE PRECISION,
    atr                 DOUBLE PRECISION,
    trend               TEXT,
    htf_alignment       TEXT,
    mtf_state           TEXT,
    impulse             BOOLEAN,
    pullback            BOOLEAN,
    retest              BOOLEAN,
    sd_state            TEXT,
    rvol                DOUBLE PRECISION,
    bar_index           INT,
    payload             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (dataset_version, feature_version, symbol, timeframe, timestamp, direction)
);

CREATE TABLE IF NOT EXISTS research_gap_reports (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,
    timeframe           TEXT NOT NULL,
    kind                TEXT NOT NULL DEFAULT 'GAP_DETECTED',
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ NOT NULL,
    duration_ms         BIGINT,
    expected_candles    INT,
    missing_candles     INT,
    dataset_version     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_research_bos_events_lookup ON research_bos_events (symbol, timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_research_bos_events_dataset ON research_bos_events (dataset_version, feature_version, symbol);
CREATE INDEX IF NOT EXISTS idx_research_gap_reports_sym ON research_gap_reports (symbol, timeframe, start_time);
CREATE INDEX IF NOT EXISTS idx_research_manifest_status ON research_data_manifest (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf_time ON ohlcv (symbol, timeframe, time);

-- System diagnostics / Issue Center
CREATE TABLE IF NOT EXISTS diagnostic_issues (
    id                  TEXT PRIMARY KEY,
    diagnostic_id       TEXT NOT NULL,
    fingerprint         TEXT NOT NULL UNIQUE,
    severity            TEXT NOT NULL,
    status              TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT 'APPLICATION',
    service             TEXT,
    subsystem           TEXT,
    component           TEXT,
    module              TEXT,
    file                TEXT,
    function            TEXT,
    line                INT,
    error_code          TEXT,
    message             TEXT NOT NULL DEFAULT '',
    exception_type      TEXT,
    stack_trace         TEXT,
    symbol              TEXT,
    timeframe           TEXT,
    provider            TEXT,
    endpoint            TEXT,
    stream              TEXT,
    request_id          TEXT,
    job_id              TEXT,
    run_id              TEXT,
    expected            TEXT,
    actual              TEXT,
    first_seen          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurrence_count    INT NOT NULL DEFAULT 1,
    resolved_at         TIMESTAMPTZ,
    resolution_message  TEXT,
    owner               TEXT,
    details             JSONB NOT NULL DEFAULT '{}',
    metadata            JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS diagnostic_events (
    id                  TEXT PRIMARY KEY,
    diagnostic_id       TEXT NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'OPEN',
    service             TEXT,
    subsystem           TEXT,
    component           TEXT,
    module              TEXT,
    file                TEXT,
    function            TEXT,
    line                INT,
    event_type          TEXT,
    error_code          TEXT,
    category            TEXT NOT NULL DEFAULT 'APPLICATION',
    message             TEXT NOT NULL DEFAULT '',
    details             JSONB NOT NULL DEFAULT '{}',
    exception_type      TEXT,
    stack_trace         TEXT,
    symbol              TEXT,
    timeframe           TEXT,
    provider            TEXT,
    endpoint            TEXT,
    stream              TEXT,
    request_id          TEXT,
    job_id              TEXT,
    run_id              TEXT,
    expected            TEXT,
    actual              TEXT,
    fingerprint         TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS diagnostic_snapshots (
    id                  TEXT PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    label               TEXT,
    git_commit          TEXT,
    git_branch          TEXT,
    status              TEXT NOT NULL DEFAULT 'CREATED',
    size_bytes          BIGINT,
    checksum            TEXT,
    checksum_algorithm  TEXT DEFAULT 'sha256',
    artifact_path       TEXT,
    summary             JSONB NOT NULL DEFAULT '{}',
    payload             JSONB NOT NULL DEFAULT '{}',
    immutable           BOOLEAN NOT NULL DEFAULT TRUE,
    verification_status TEXT,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS diagnostic_service_checks (
    id                  BIGSERIAL PRIMARY KEY,
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    service_key         TEXT NOT NULL,
    status              TEXT NOT NULL,
    reason              TEXT,
    latency_ms          DOUBLE PRECISION,
    metrics             JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS diagnostic_resource_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    sampled_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    disk                JSONB NOT NULL DEFAULT '{}',
    memory              JSONB NOT NULL DEFAULT '{}',
    cpu                 JSONB NOT NULL DEFAULT '{}',
    process             JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS diagnostic_backup_runs (
    id                  TEXT PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    backup_type         TEXT NOT NULL,
    status              TEXT NOT NULL,
    location            TEXT,
    destination_dir     TEXT,
    size_bytes          BIGINT,
    checksum            TEXT,
    checksum_algorithm  TEXT DEFAULT 'sha256',
    verified            BOOLEAN NOT NULL DEFAULT FALSE,
    verification_status TEXT,
    validation_status   TEXT,
    restore_test_status TEXT,
    tool                TEXT,
    tool_version        TEXT,
    git_commit          TEXT,
    run_id              TEXT,
    database_name       TEXT,
    duration_ms         DOUBLE PRECISION,
    error               TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_diag_issues_status ON diagnostic_issues (status, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_diag_issues_severity ON diagnostic_issues (severity, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_diag_issues_component ON diagnostic_issues (component, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_diag_issues_diagnostic_id ON diagnostic_issues (diagnostic_id);
CREATE INDEX IF NOT EXISTS idx_diag_issues_fingerprint ON diagnostic_issues (fingerprint);
CREATE INDEX IF NOT EXISTS idx_diag_issues_job ON diagnostic_issues (job_id);
CREATE INDEX IF NOT EXISTS idx_diag_events_ts ON diagnostic_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_diag_events_fingerprint ON diagnostic_events (fingerprint, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_diag_events_diagnostic_id ON diagnostic_events (diagnostic_id);
CREATE INDEX IF NOT EXISTS idx_diag_events_severity ON diagnostic_events (severity, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_diag_snapshots_created ON diagnostic_snapshots (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_service_checks_key ON diagnostic_service_checks (service_key, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_resource_sampled ON diagnostic_resource_snapshots (sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_diag_backup_started ON diagnostic_backup_runs (started_at DESC);

-- Dynamic COMBO_02 v2 research candidate pipeline (never joins v1 universe)
CREATE TABLE IF NOT EXISTS strategy_candidate_registry (
    id                          TEXT PRIMARY KEY,
    symbol                      TEXT NOT NULL,
    market_type                 TEXT NOT NULL DEFAULT 'futures_perp',
    quote_asset                 TEXT NOT NULL DEFAULT 'USDT',
    discovered_at_utc           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_screened_at_utc        TIMESTAMPTZ,
    state                       TEXT NOT NULL DEFAULT 'DISCOVERED',
    state_reason                TEXT,
    state_updated_at_utc        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    selector_version            TEXT,
    manifest_id                 TEXT,
    discovery_rank              INT,
    discovery_volume_usd        DOUBLE PRECISION,
    discovery_reason            TEXT,
    ohlcv_1h_start_utc          TIMESTAMPTZ,
    ohlcv_1h_end_utc            TIMESTAMPTZ,
    ohlcv_1h_completeness       DOUBLE PRECISION,
    ohlcv_4h_start_utc          TIMESTAMPTZ,
    ohlcv_4h_end_utc            TIMESTAMPTZ,
    ohlcv_4h_completeness       DOUBLE PRECISION,
    history_days                DOUBLE PRECISION,
    data_health_checked_at_utc  TIMESTAMPTZ,
    data_health_block_reason    TEXT,
    backtest_window_start_utc   TEXT,
    backtest_window_end_utc     TEXT,
    backtest_engine_fingerprint JSONB NOT NULL DEFAULT '{}',
    backtest_status             TEXT,
    backtest_tier               TEXT,
    backtest_trade_count        INT,
    backtest_win_rate           DOUBLE PRECISION,
    backtest_net_avg_r          DOUBLE PRECISION,
    backtest_profit_factor      DOUBLE PRECISION,
    backtest_net_pnl            DOUBLE PRECISION,
    backtest_fees               DOUBLE PRECISION,
    backtest_max_dd_r           DOUBLE PRECISION,
    backtest_max_losing_streak  INT,
    oos_status                  TEXT,
    oos_window_start_utc        TEXT,
    oos_window_end_utc          TEXT,
    oos_trade_count             INT,
    oos_net_avg_r               DOUBLE PRECISION,
    oos_profit_factor           DOUBLE PRECISION,
    oos_net_pnl                 DOUBLE PRECISION,
    oos_max_dd_r                DOUBLE PRECISION,
    oos_max_losing_streak       INT,
    portfolio_overlap_btc       DOUBLE PRECISION,
    portfolio_overlap_eth       DOUBLE PRECISION,
    portfolio_overlap_sol       DOUBLE PRECISION,
    peak_concurrent_positions   INT,
    portfolio_incremental_dd_r  DOUBLE PRECISION,
    portfolio_report            JSONB NOT NULL DEFAULT '{}',
    risk_percent                DOUBLE PRECISION NOT NULL DEFAULT 0,
    operator_approved           BOOLEAN NOT NULL DEFAULT FALSE,
    operator_approved_by        TEXT,
    operator_approved_at_utc    TIMESTAMPTZ,
    approval_note               TEXT,
    strategy_id                 TEXT NOT NULL DEFAULT 'COMBO_02_V2_RESEARCH',
    combo_version               TEXT NOT NULL DEFAULT 'v2-research',
    source                      TEXT NOT NULL DEFAULT 'DYNAMIC_CANDIDATE_PIPELINE',
    telegram_eligible           BOOLEAN NOT NULL DEFAULT FALSE,
    production_approved         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, strategy_id)
);

CREATE TABLE IF NOT EXISTS strategy_candidate_audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    candidate_id        TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    action              TEXT NOT NULL,
    actor               TEXT,
    previous_state      TEXT,
    new_state           TEXT,
    previous_risk       DOUBLE PRECISION,
    new_risk            DOUBLE PRECISION,
    note                TEXT,
    payload             JSONB NOT NULL DEFAULT '{}',
    created_at_utc      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scr_state ON strategy_candidate_registry (state, updated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_scr_symbol ON strategy_candidate_registry (symbol);
CREATE INDEX IF NOT EXISTS idx_scr_audit_sym ON strategy_candidate_audit_log (symbol, created_at_utc DESC);

