import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    eng = create_async_engine(
        "postgresql+asyncpg://screener:screener@localhost:5432/cryptoscreener"
    )
    async with eng.connect() as c:
        idle = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND state='idle in transaction'"
                )
            )
        ).scalar()
        locks = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type='Lock'"
                )
            )
        ).scalar()
        print({"idle_in_tx": idle, "lock_waits": locks})
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
