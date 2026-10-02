from __future__ import annotations

import asyncio

from app.config import Settings
from app.ingestion.providers.onchain import OnChainProvider
from app.ingestion.providers.sentiment import SentimentProvider
from app.models.schemas import DataStatus


def test_onchain_returns_waiting():
    settings = Settings()
    p = OnChainProvider(settings)
    holders = asyncio.run(p.get_holder_metrics(["BTCUSDT"]))
    assert holders["BTCUSDT"]["holder_count"].status == DataStatus.WAITING
    assert holders["BTCUSDT"]["top10_pct"].status == DataStatus.WAITING
    assert holders["BTCUSDT"]["top_10_holder_pct"].status == DataStatus.WAITING
    txs = asyncio.run(p.get_transaction_metrics(["BTCUSDT"]))
    assert txs["BTCUSDT"]["tx_volume"].status == DataStatus.WAITING
    assert txs["BTCUSDT"]["transaction_volume_usd"].status == DataStatus.WAITING
    assert asyncio.run(p.get_nvt("BTCUSDT")).status == DataStatus.WAITING
    assert asyncio.run(p.get_velocity("BTCUSDT")).status == DataStatus.WAITING


def test_sentiment_never_fabricated():
    settings = Settings()
    p = SentimentProvider(settings)
    sent = asyncio.run(p.get_sentiment(["ETHUSDT"]))
    for _k, fv in sent["ETHUSDT"].items():
        assert fv.value is None
        assert fv.status == DataStatus.WAITING
