"""Create local Postgres role/db for cryptoscreener (dev acceptance)."""

from __future__ import annotations

import asyncio

import asyncpg


async def main() -> None:
    admin = await asyncpg.connect(
        user="postgres",
        password="postgres",
        host="127.0.0.1",
        port=5432,
        database="postgres",
    )
    try:
        roles = await admin.fetch("SELECT 1 FROM pg_roles WHERE rolname = 'screener'")
        if not roles:
            await admin.execute("CREATE ROLE screener LOGIN PASSWORD 'screener'")
            print("created role screener")
        else:
            await admin.execute("ALTER ROLE screener WITH LOGIN PASSWORD 'screener'")
            print("updated role screener")
        dbs = await admin.fetch(
            "SELECT 1 FROM pg_database WHERE datname = 'cryptoscreener'"
        )
        if not dbs:
            await admin.execute("CREATE DATABASE cryptoscreener OWNER screener")
            print("created database cryptoscreener")
        else:
            print("database already exists")
    finally:
        await admin.close()

    conn = await asyncpg.connect(
        user="screener",
        password="screener",
        host="127.0.0.1",
        port=5432,
        database="cryptoscreener",
    )
    try:
        await conn.execute("SELECT 1")
        print("screener login ok")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
