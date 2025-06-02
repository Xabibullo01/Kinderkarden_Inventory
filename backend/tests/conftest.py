import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, get_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import User, Product, Meal, MealIngredient, MealServing, InventoryLog
from app.schemas import UserCreate, ProductCreate, MealCreate, MealServingCreate
from datetime import datetime
from celery import Celery
from app.celery_app import celery_app
import aiosqlite
from sqlalchemy import text

app.router.on_startup.clear()


# Configure Celery for tests
celery_app.conf.update(
    broker='redis://redis:6379/0',  # Updated to match Dockerized Redis
    backend='redis://redis:6379/0',
    task_always_eager=True,
    task_eager_propagates=True
)

# Use an in-memory SQLite database for testing with async
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(SQLALCHEMY_DATABASE_URL)
AsyncTestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


# Create tables
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Async fixture for database
@pytest_asyncio.fixture(scope="session")
async def db_session():
    await setup_database()
    async with AsyncTestingSessionLocal() as session:
        yield session
    # Cleanup (drop tables) - optional, since it's in-memory
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)



@pytest.fixture(scope="module")
def client(db_session):
    async def get_db_override():
        yield db_session
    app.dependency_overrides[get_db] = get_db_override
    with TestClient(app) as c:
        yield c

@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_teardown(db_session: AsyncSession):
    # Setup: Clear and initialize test data
    await db_session.execute(text("DELETE FROM meal_servings"))
    await db_session.execute(text("DELETE FROM inventory_logs"))
    await db_session.execute(text("DELETE FROM meal_ingredients"))
    await db_session.execute(text("DELETE FROM meals"))
    await db_session.execute(text("DELETE FROM products"))
    await db_session.execute(text("DELETE FROM users"))

    # Create test users with hashed passwords
    from app.auth import get_password_hash
    admin_password = "adminpass"
    admin_hash = get_password_hash(admin_password)
    admin_user = User(username="adminuser", password_hash=admin_hash, role="admin")
    cook_password = "cookpass"
    cook_hash = get_password_hash(cook_password)
    cook_user = User(username="cookuser", password_hash=cook_hash, role="cook")
    db_session.add_all([admin_user, cook_user])
    await db_session.commit()

    # Create test products
    product = Product(name="Milk", quantity=100.0, threshold=10.0, delivery_date=datetime.now())
    db_session.add(product)
    await db_session.commit()

    # Create test meal
    meal = Meal(name="Breakfast")
    db_session.add(meal)
    await db_session.commit()
    ingredient = MealIngredient(meal_id=meal.id, product_id=product.id, quantity=50.0)
    db_session.add(ingredient)
    await db_session.commit()

    yield

    # Teardown: Rollback any changes
    await db_session.rollback()

@pytest.fixture
async def admin_token(client, db_session):
    response = client.post("/login", data={"username": "adminuser", "password": "adminpass"})
    assert await response.status_code == 200
    return response.json()["access_token"]

@pytest.fixture
async def cook_token(client, db_session):
    response = client.post("/login", data={"username": "cookuser", "password": "cookpass"})
    assert response.status_code == 200
    return response.json()["access_token"]