from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def migrate_webhook_endpoints(sync_conn) -> None:
            """Small additive migration for installations created before scoped webhooks."""
            columns = {column["name"] for column in inspect(sync_conn).get_columns("webhook_endpoints")}
            additions = {
                "device_id": "INTEGER",
                "format": "VARCHAR(30) DEFAULT 'normalized'",
                "bearer_token": "VARCHAR(1000)",
            }
            for name, sql_type in additions.items():
                if name not in columns:
                    sync_conn.execute(text(f"ALTER TABLE webhook_endpoints ADD COLUMN {name} {sql_type}"))

        await conn.run_sync(migrate_webhook_endpoints)


async def get_db():
    async with SessionLocal() as session:
        yield session

