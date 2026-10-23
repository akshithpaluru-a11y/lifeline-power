"""SQLite database access (SQLAlchemy 2.0, async via aiosqlite)."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.models import Base


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        event.listen(self.engine.sync_engine, "connect", _configure_sqlite)
        # Objects stay usable after commit, so handlers can build responses from them.
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_tables(self) -> None:
        """Create any missing tables (the schema is small enough not to need migrations)."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Per-connection SQLite settings: enforce foreign keys and use write-ahead logging.

    WAL lets the SSE stream and the ticker read while a request writes.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
