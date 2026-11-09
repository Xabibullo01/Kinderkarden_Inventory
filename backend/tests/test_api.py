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

class _FakePubSub:
    """Stands in for redis pub/sub: yields one inventory message, then stops."""

    async def subscribe(self, channel):
        self.channel = channel

    async def unsubscribe(self, channel):
        pass

    async def listen(self):
        yield {"type": "subscribe", "channel": self.channel, "data": 1}
        yield {"type": "message", "channel": self.channel, "data": "Inventory updated: 50.0g of 1"}


class _FakeRedis:
    def pubsub(self):
        return _FakePubSub()

    async def aclose(self):
        pass


def test_websocket_inventory_endpoints(session_factory, monkeypatch):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.auth import create_access_token

    async def _get_db():
        async with session_factory() as session:
            yield session

    async def _noop_init_db():
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    monkeypatch.setattr(main_module.redis, "from_url", lambda *a, **kw: _FakeRedis())
    app.dependency_overrides[get_db] = _get_db
    try:
        with TestClient(app) as tc:
            # invalid token -> closed with policy violation
            with pytest.raises(WebSocketDisconnect):
                with tc.websocket_connect("/ws/inventory?token=invalid") as ws:
                    ws.receive_json()

            # cook is not allowed to watch inventory
            cook_token = create_access_token({"sub": "cookuser"})
            with pytest.raises(WebSocketDisconnect):
                with tc.websocket_connect(f"/ws/inventory?token={cook_token}") as ws:
                    ws.receive_json()

            # admin receives the published inventory update
            admin_token = create_access_token({"sub": "adminuser"})
            with tc.websocket_connect(f"/ws/inventory?token={admin_token}") as ws:
                data = ws.receive_json()
                assert data["channel"] == "inventory_updates"
                assert "Inventory updated" in data["data"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_update_and_delete_meal_and_product(client, admin_token):
    """Regression test: these used an invalid select().delete() and an un-awaited db.delete()."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    r = await client.put("/meals/1", json={"name": "Big Breakfast", "ingredients": [{"meal_id": 1, "product_id": 1, "quantity": 20.0}]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Big Breakfast"

    r = await client.delete("/meals/1", headers=headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/meals/1", headers=headers)).status_code == 404

    r = await client.delete("/products/1", headers=headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/products/1", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_all_main_routes_are_registered(client):
    """Regression test: a second FastAPI() at the end of main.py used to hide these routes."""
    paths = {route.path for route in client._transport.app.routes}
    for expected in ["/register", "/login", "/meals", "/serve-meal", "/usage-report", "/ws/inventory", "/patterns/actions"]:
        assert expected in paths


@pytest.mark.asyncio
async def test_cannot_self_register_as_admin_or_manager(client, cook_token):
    """Security: anonymous users and non-admins must not be able to create privileged accounts."""
    for role in ("admin", "manager"):
        r = await client.post("/register", json={"username": f"hacker_{role}", "password": "hackpass1", "role": role})
        assert r.status_code == 403, r.text

        r = await client.post(
            "/register",
            json={"username": f"cook_made_{role}", "password": "hackpass1", "role": role},
            headers={"Authorization": f"Bearer {cook_token}"},
        )
        assert r.status_code == 403, r.text

    # public sign-up as cook is still allowed
    r = await client.post("/register", json={"username": "newcook", "password": "cookpass1", "role": "cook"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_create_manager(client, admin_token):
    r = await client.post(
        "/register",
        json={"username": "newmanager", "password": "managerpass", "role": "manager"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "manager"
