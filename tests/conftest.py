import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test-ebio.db"
os.environ["ADMIN_USERNAME"] = "test"
os.environ["ADMIN_PASSWORD"] = "test"

import pytest
from httpx import ASGITransport, AsyncClient

from app.database import engine
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
async def clean_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value

