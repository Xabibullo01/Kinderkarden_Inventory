import pytest
from sqlalchemy import select
from httpx import AsyncClient
import asyncio
import websockets
from app.schemas import ProductCreate, MealCreate, MealServingCreate
from app.models import Product, Meal, MealIngredient, MealServing, InventoryLog
from datetime import datetime, timedelta
from celery.result import AsyncResult
import json

@pytest.mark.asyncio
async def test_register(client, admin_token):
    admin = {"Authorization": f"Bearer {admin_token}"}
    response = await client.post("/register", json={"username": "testuser", "password": "testpass123", "role": "manager"}, headers=admin)
    assert response.status_code == 200
    assert response.json()["message"] == "User registered successfully"
    assert response.json()["role"] == "manager"

    response = await client.post("/register", json={"username": "testuser", "password": "testpass123", "role": "manager"}, headers=admin)
    assert response.status_code == 400
    assert response.json()["detail"] == "Username already registered"

    response = await client.post("/register", json={"username": "testuser2", "password": "pass", "role": "invalid"})
    assert response.status_code == 422
    response = await client.post("/register", json={"username": "testuser3", "password": "testpass", "role": "cook"})
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_login(client, db_session):
    response = await client.post("/login", data={"username": "adminuser", "password": "adminpass"})
    assert response.status_code == 200
    assert "access_token" in response.json()

    response = await client.post("/login", data={"username": "adminuser", "password": "wrongpass"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"

@pytest.mark.asyncio
async def test_refresh_token(client, admin_token):
    response = await client.post("/refresh", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert response.json()["expires_in"] == 1800

@pytest.mark.asyncio
async def test_products_endpoints(client, admin_token, cook_token, db_session):
    response = await client.get("/products?skip=0&limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    print(f"GET /products response: {response.json()}")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    response = await client.post("/products", json={"name": "Bread", "quantity": -1.0, "threshold": 5.0}, headers={"Authorization": f"Bearer {admin_token}"})
    print(f"POST /products (invalid) response: {response.json()}")
    assert response.status_code == 422
    product_data = {"name": "Bread", "quantity": 50.0, "threshold": 5.0}
    response = await client.post("/products", json=product_data, headers={"Authorization": f"Bearer {admin_token}"})
    print(f"POST /products response: {response.json()}")
    assert response.status_code == 200
    product_id = response.json()["id"]
    assert response.json()["name"] == "Bread"

    response = await client.get(f"/products/{product_id}", headers={"Authorization": f"Bearer {admin_token}"})
    print(f"GET /products/{product_id} response: {response.json()}")
    assert response.status_code == 200
    updated_data = {"name": "Bread Updated", "quantity": 60.0, "threshold": 6.0}
    response = await client.put(f"/products/{product_id}", json=updated_data, headers={"Authorization": f"Bearer {admin_token}"})
    print(f"PUT /products/{product_id} response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["quantity"] == 60.0
    response = await client.delete(f"/products/{product_id}", headers={"Authorization": f"Bearer {admin_token}"})
    print(f"DELETE /products/{product_id} response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["message"] == "Product deleted"

    response = await client.get("/products", headers={"Authorization": f"Bearer {cook_token}"})
    print(f"GET /products (unauthorized) response: {response.json()}")
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_meals_endpoints(client, admin_token, cook_token, db_session):
    response = await client.get("/meals?skip=0&limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    print(f"GET /meals response: {response.json()}")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    unique_meal_name = f"Lunch_{int(datetime.now().timestamp())}"
    meal_data = {"name": unique_meal_name, "ingredients": [{"meal_id": 1, "product_id": 1, "quantity": 30.0}]}
    response = await client.post("/meals", json=meal_data, headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["name"] == unique_meal_name

    response = await client.get("/meals?skip=0&limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_serve_meal(client, admin_token, cook_token, db_session):
    meal_serving = MealServingCreate(meal_id=1)
    response = await client.post("/serve-meal", json=meal_serving.dict(), headers={"Authorization": f"Bearer {cook_token}"})
    print(f"POST /serve-meal response: {response.json()}")
    assert response.status_code == 200
    assert response.json()["meal_id"] == 1
    product = await db_session.execute(select(Product).filter(Product.id == 1))
    product = product.scalars().first()
    print(f"Product quantity after serving: {product.quantity}")
    assert product.quantity == 50.0  # 100 - 50

    # 50g left and the meal needs 50g -> one more serving is still possible
    response = await client.post("/serve-meal", json=meal_serving.model_dump(), headers={"Authorization": f"Bearer {cook_token}"})
    assert response.status_code == 200

    # stock is now 0 -> not enough for another portion
    response = await client.post("/serve-meal", json=meal_serving.model_dump(), headers={"Authorization": f"Bearer {cook_token}"})
    assert response.status_code == 400
    assert "Insufficient quantity" in response.json()["detail"]

    # managers can view reports but are not allowed to serve meals
    await client.post("/register", json={"username": "manager1", "password": "managerpass", "role": "manager"}, headers={"Authorization": f"Bearer {admin_token}"})
    login = await client.post("/login", data={"username": "manager1", "password": "managerpass"})
    manager_token = login.json()["access_token"]
    response = await client.post("/serve-meal", json=meal_serving.model_dump(), headers={"Authorization": f"Bearer {manager_token}"})
    assert response.status_code == 403

@pytest.mark.asyncio
async def test_portion_estimates(client, admin_token):
    response = await client.get("/portion-estimates", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert any(m["meal_id"] == 1 for m in response.json())

@pytest.mark.asyncio
async def test_generate_report(client, admin_token, db_session, monkeypatch):
    log = InventoryLog(product_id=1, change_type="delivery", quantity=10.0, user_id=1, timestamp=datetime.now())
    db_session.add(log)
    await db_session.commit()

    response = await client.post("/generate-report", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    assert task_id

    from app.celery_app import celery_app
    from tests.conftest import _DummyResult
    monkeypatch.setattr(celery_app, "AsyncResult", lambda tid: _DummyResult({"report": {"total_used": 10.0}}))
    response = await client.get(f"/report/{task_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert "report" in response.json()["result"]

@pytest.mark.asyncio
async def test_notifications(client, admin_token, monkeypatch):
    response = await client.get("/notifications", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert "low_inventory" in response.json()
    task_id = response.json()["discrepancy_task_id"]
    assert task_id

    from app.celery_app import celery_app
    from tests.conftest import _DummyResult
    monkeypatch.setattr(celery_app, "AsyncResult", lambda tid: _DummyResult({"discrepancy_rate": 0.0}))
    response = await client.get(f"/discrepancy/{task_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    assert "discrepancy_rate" in response.json()["result"]

@pytest.mark.asyncio
async def test_usage_report(client, admin_token, db_session):
    log = InventoryLog(product_id=1, change_type="consumption", quantity=10.0, user_id=1, timestamp=datetime.now())
    db_session.add(log)
    await db_session.commit()

    response = await client.get("/usage-report", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert len(response.json()["usage"]) > 0

    response = await client.get("/usage-report?start_date=2025-05-01&end_date=2025-05-24", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_servings_log(client, admin_token, db_session):
    serving = MealServing(meal_id=1, user_id=1, timestamp=datetime.now())
    db_session.add(serving)
    await db_session.commit()

    response = await client.get("/servings-log?skip=0&limit=10", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = await client.get("/servings-log?start_date=2025-05-01&end_date=2025-05-24", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_serve_meal_publishes_inventory_update(client, cook_token, published_messages):
    """Serving a meal must publish an update (consumed by the /ws/inventory socket)."""
    response = await client.post("/serve-meal", json={"meal_id": 1}, headers={"Authorization": f"Bearer {cook_token}"})
    assert response.status_code == 200
    assert published_messages and "Inventory updated" in published_messages[0]
