from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    await redis_manager.connect(settings)
    if redis_manager.client is not None:
        await market_store.connect_redis(redis_manager.client)

    await db_manager.connect(settings)

    ingestion = MarketDataIngestionService(settings, market_store)
    ingestion_mod.ingestion_service = ingestion
    await ingestion.start()

    yield

    await ingestion.stop()
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
    app.include_router(ws_router, prefix="/ws")
    return app


app = create_app()
