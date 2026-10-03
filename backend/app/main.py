from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.diagnostics_routes import router as diagnostics_router
from app.api.routes import router, ws_router
from app.config import ROOT, get_settings
from app.core.logging import get_logger, setup_logging
from app.ingestion import service as ingestion_mod
from app.ingestion.service import MarketDataIngestionService
from app.services.database import db_manager
from app.services.market_store import market_store
from app.services.redis_manager import redis_manager

load_dotenv(ROOT / ".env")
load_dotenv(Path.cwd() / ".env")

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "starting",
        use_real_data=settings.use_real_data,
        redis_enabled=settings.redis_enabled,
        database_enabled=settings.database_enabled,
    )

    if not settings.use_real_data:
        logger.error(
            "USE_REAL_DATA is false — refusing mock market data mode in this build"
        )

    t_app = time.perf_counter()
    await redis_manager.connect(settings)
    if redis_manager.client is not None:
        await market_store.connect_redis(redis_manager.client)

    await db_manager.connect(settings)
    if not db_manager.schema_ready and settings.database_enabled:
        logger.error(
            "startup_schema_not_ready",
            status=db_manager.status,
            last_schema_error=db_manager.last_schema_error,
            stages=db_manager.startup_stages[-10:],
        )

    # WS event-loop lag monitor (diagnostic only — does not alter reconnect policy)
    from app.ingestion.ws_forensics import ws_forensics

    await ws_forensics.event_loop.start()

    ingestion = MarketDataIngestionService(settings, market_store)
    ingestion_mod.ingestion_service = ingestion
    await ingestion.start()
    logger.info(
        "startup_stage",
        startup_stage="APPLICATION_STARTUP",
        status="OK",
        duration_ms=round((time.perf_counter() - t_app) * 1000.0, 1),
        schema_ready=db_manager.schema_ready,
    )

    yield

    await ingestion.stop()
    await ws_forensics.event_loop.stop()
    await db_manager.close()
    await redis_manager.close()
    logger.info("shutdown_complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Crypto Screener API",
        version="0.1.0",
        description="Production real-time crypto screener — live market data only.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")
    app.include_router(diagnostics_router, prefix="/api")
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()
