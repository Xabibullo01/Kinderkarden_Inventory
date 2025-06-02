import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

import app.main as main_module
from app.main import app
from app.database import Base, get_db
from app.models import User, Product, Meal, MealIngredient, MealServing, InventoryLog
from app.auth import get_password_hash
from app.celery_app import celery_app

# --- 1) Set up in-memory SQLite and override get_db ----
SQLALCHEMY_TEST_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(SQLALCHEMY_TEST_URL, echo=False)
TestingSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

@pytest_asyncio.fixture(scope="session", autouse=True)
async def prepare_database():
    # create all tables once
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # drop all at end
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, monkeypatch):
     # override init_db to a no-op so our TestClient never hits the real DATABASE_URL
     import app.main as main_module
     async def _noop_init_db():
         return
     monkeypatch.setattr(main_module, "init_db", _noop_init_db)

     # override the DB dependency to use our in-memory SQLite
     async def _get_db():
         yield db_session
     app.dependency_overrides[get_db] = _get_db

     # patch celery to synchronous dummy
     class DummyTask:
         def __init__(self, id): self.id = id
     monkeypatch.setattr(celery_app, 'send_task', lambda name, *args, **kwargs: DummyTask(id="task-123"))
     class DummyResult:
         def __init__(self, result): self._result = result
         def ready(self): return True
         def failed(self): return False
         def get(self, propagate=True): return self._result
     monkeypatch.setattr(celery_app, 'AsyncResult', lambda tid: DummyResult({"foo": "bar"}))

     transport = ASGITransport(app=app)
     async with AsyncClient(transport=transport, base_url="http://test") as ac:
         yield ac


# --- 2) Seed a clean state before each test ---
@pytest_asyncio.fixture(autouse=True)
async def seed_data(db_session: AsyncSession):
    # wipe every table
    for tbl in (
        "meal_servings", "inventory_logs", "meal_ingredients",
        "meals", "products", "users"
    ):
        await db_session.execute(text(f"DELETE FROM {tbl}"))
    await db_session.commit()

    # create two users: admin & cook
    admin = User(
        username="adminuser",
        password_hash=get_password_hash("adminpass"),
        role="admin"
    )
    cook = User(
        username="cookuser",
        password_hash=get_password_hash("cookpass"),
        role="cook"
    )
    db_session.add_all([admin, cook])
    await db_session.commit()

    # one product + one meal + one ingredient
    prod = Product(
        name="Milk",
        quantity=100.0,
        threshold=10.0,
        delivery_date=datetime.now()
    )
    meal = Meal(name="Breakfast")
    db_session.add_all([prod, meal])
    await db_session.commit()

    ing = MealIngredient(
        meal_id=meal.id,
        product_id=prod.id,
        quantity=50.0
    )
    db_session.add(ing)
    await db_session.commit()

    yield
    await db_session.rollback()

# --- 3) Helpers to grab JWT tokens ---
@pytest_asyncio.fixture
async def admin_token(client: AsyncClient):
    r = await client.post(
        "/login",
        data={"username":"adminuser","password":"adminpass"}
    )
    assert r.status_code == 200
    return r.json()["access_token"]

@pytest_asyncio.fixture
async def cook_token(client: AsyncClient):
    r = await client.post(
        "/login",
        data={"username":"cookuser","password":"cookpass"}
    )
    assert r.status_code == 200
    return r.json()["access_token"]

# --- 4) Comprehensive Tests ---

@pytest.mark.asyncio
async def test_invalid_and_duplicate_registration(client):
    # invalid role
    r = await client.post(
        "/register",
        json={"username":"foo","password":"barbaz","role":"bogus"}
    )
    assert r.status_code == 400

    # duplicate username
    r2 = await client.post(
        "/register",
        json={"username":"adminuser","password":"barbaz","role":"admin"}
    )
    assert r2.status_code == 400

@pytest.mark.asyncio
async def test_login_wrong_password(client):
    r = await client.post(
        "/login",
        data={"username":"adminuser","password":"wrongpass"}
    )
    assert r.status_code == 401

@pytest.mark.asyncio
async def test_products_crud_and_authorization(client, admin_token, cook_token):
    # admin can list & create
    r = await client.get(
        "/products?skip=0&limit=10",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)

    # cook cannot create
    r2 = await client.post(
        "/products",
        json={"name":"Eggs","quantity":20,"threshold":5,"delivery_date":str(datetime.now())},
        headers={"Authorization":f"Bearer {cook_token}"}
    )
    assert r2.status_code == 403

@pytest.mark.asyncio
async def test_meal_delete_with_existing_serving(client, admin_token, db_session):
    # add a serving for meal id 1
    ms = MealServing(meal_id=1, user_id=1)
    db_session.add(ms)
    await db_session.commit()

    # attempt to delete
    r = await client.delete(
        "/meals/1",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 400

@pytest.mark.asyncio
async def test_portion_estimates_zero_ingredient(client, admin_token, db_session):
    # create an empty meal
    m2 = Meal(name="EmptyMeal")
    db_session.add(m2)
    await db_session.commit()

    r = await client.get(
        "/portion-estimates",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    data = r.json()
    # find the empty meal
    empty = next(x for x in data if x["meal_id"] == m2.id)
    assert empty["portions"] == 0

@pytest.mark.asyncio
async def test_generate_and_fetch_report_endpoints(client, admin_token):
    # trigger generation
    r = await client.post(
        "/generate-report",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    tid = r.json()["task_id"]

    # fetch report
    r2 = await client.get(f"/report/{tid}", headers={"Authorization":f"Bearer {admin_token}"})
    assert r2.status_code == 200
    js = r2.json()
    assert js["status"] == "SUCCESS"
    assert "result" in js

@pytest.mark.asyncio
async def test_notifications_and_discrepancy_endpoints(client, admin_token, db_session):
    # ensure low inventory
    low = Product(name="LowProd", quantity=1, threshold=5, delivery_date=datetime.now())
    db_session.add(low)
    await db_session.commit()

    # notifications
    r = await client.get(
        "/notifications",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    tid = r.json()["discrepancy_task_id"]

    # fetch discrepancy
    r2 = await client.get(f"/discrepancy/{tid}", headers={"Authorization":f"Bearer {admin_token}"})
    assert r2.status_code == 200
    js = r2.json()
    assert js["status"] == "SUCCESS"
    assert "result" in js

@pytest.mark.asyncio
async def test_usage_report_date_filtering(client, admin_token, db_session):
    # old log
    old = InventoryLog(
        product_id=1, change_type="delivery", quantity=5.0,
        user_id=1, timestamp=datetime(2000,1,1)
    )
    db_session.add(old)
    await db_session.commit()

    # filter start_date after old
    r = await client.get(
        "/usage-report?start_date=2001-01-01",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert r.json()["usage"] == {}

@pytest.mark.asyncio
async def test_servings_log(client, admin_token, db_session):
    # add serving
    sv = MealServing(meal_id=1, user_id=1)
    db_session.add(sv)
    await db_session.commit()

    r = await client.get(
        "/servings-log?skip=0&limit=10",
        headers={"Authorization":f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)

@pytest.mark.asyncio
async def test_websocket_inventory_endpoints(client, cook_token, admin_token):
    from starlette.testclient import TestClient

    # cook is not authorized → handshake still succeeds but server immediately closes.
    with TestClient(app) as tc:
        ws = tc.websocket_connect(f"/ws/inventory?token={cook_token}", timeout=1)
        # as soon as we try to receive, it will have been closed:
        with pytest.raises(Exception):
            ws.receive_json()

    # admin *is* authorized → we can open the socket and then close cleanly
    with TestClient(app) as tc:
        with tc.websocket_connect(f"/ws/inventory?token={admin_token}") as ws:
            # the connection object should report CONNECTED
            assert ws.client_state == ws.client_state.CONNECTED
            # then explicitly close from client side
            ws.close()

