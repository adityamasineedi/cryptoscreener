import asyncio

import asyncpg


async def main() -> None:
    c = await asyncpg.connect(
        user="screener",
        password="screener",
        host="127.0.0.1",
        port=5432,
        database="cryptoscreener",
    )
    tables = await c.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
    )
    print("tables", [r[0] for r in tables])
    for t in [
        "ohlcv",
        "volume_history",
        "price_snapshots",
        "funding_rates",
        "open_interest",
        "liquidations",
        "signals",
        "structure_events",
        "supply_demand_zones",
        "symbols",
    ]:
        try:
            n = await c.fetchval(f"SELECT count(*) FROM {t}")
            print(t, n)
        except Exception as e:  # noqa: BLE001
            print(t, "ERR", e)
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
