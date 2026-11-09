"""Shared pytest fixtures.

Each test gets a fresh in-memory SQLite database, an httpx AsyncClient talking to
the app in-process, and fake Celery/Redis so no external services are needed.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from datetime import datetime  # noqa: E402

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.crud as crud_module  # noqa: E402
import app.main as main_module  # noqa: E402
from app.auth import get_password_hash  # noqa: E402
from app.celery_app import celery_app  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Meal, MealIngredient, Product, User  # noqa: E402


@pytest_asyncio.fixture
async def db_engine():
    # StaticPool = one shared connection, so the in-memory DB survives between sessions.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def seed_data(session_factory):
    async with session_factory() as session:
        session.add_all([
            User(username="adminuser", password_hash=get_password_hash("adminpass"), role="admin"),
            User(username="cookuser", password_hash=get_password_hash("cookpass"), role="cook"),
        ])
        product = Product(name="Milk", quantity=100.0, threshold=10.0, delivery_date=datetime.now())
        meal = Meal(name="Breakfast")
        session.add_all([product, meal])
        await session.commit()
        session.add(MealIngredient(meal_id=meal.id, product_id=product.id, quantity=50.0))
        await session.commit()


class _DummyTask:
    def __init__(self, task_id: str):
        self.id = task_id


class _DummyResult:
    def __init__(self, result):
        self._result = result

    def ready(self):
        return True

    def failed(self):
        return False

    def get(self, propagate=True):
        return self._result


@pytest_asyncio.fixture
async def published_messages(monkeypatch):
    """Captures Redis publishes instead of talking to a real Redis."""
    messages: list[str] = []

    async def fake_publish(batch):
        messages.extend(batch)

    monkeypatch.setattr(crud_module, "publish_inventory_updates", fake_publish)
    return messages


@pytest_asyncio.fixture
async def client(session_factory, monkeypatch, published_messages):
    async def _noop_init_db():
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)

    async def _get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(celery_app, "send_task", lambda name, *a, **kw: _DummyTask("task-123"))
    monkeypatch.setattr(celery_app, "AsyncResult", lambda tid: _DummyResult({"foo": "bar"}))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _login(client: AsyncClient, username: str, password: str) -> str:
    r = await client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(client):
    return await _login(client, "adminuser", "adminpass")


@pytest_asyncio.fixture
async def cook_token(client):
    return await _login(client, "cookuser", "cookpass")
