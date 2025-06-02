import random
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import select
from app.main import app
from app.models import User

@pytest.mark.asyncio
async def test_read_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Kindergarten Meal Tracking System"}

@pytest.mark.asyncio
async def test_register_and_login(client, db_session):
    # Clear users to ensure a clean slate for this test
    await db_session.execute("DELETE FROM users")
    await db_session.commit()

    username = f"testuser_{random.randint(1, 100000)}"
    # Register a user
    response = await client.post("/register", json={"username": username, "password": "testpass123", "role": "admin"})
    assert response.status_code == 200
    assert response.json()["message"] == "User registered successfully"

    # Debug: Check the user in the database
    result = await db_session.execute(select(User).filter(User.username == username))
    user = result.scalars().first()
    assert user is not None, f"User {username} not found in database after registration"
    print(f"Stored user: {user.username}, hashed password: {user.password_hash}")

    # Login with the same username
    response = await client.post("/login", data={"username": username, "password": "testpass123"})
    assert response.status_code == 200
    assert "access_token" in response.json()