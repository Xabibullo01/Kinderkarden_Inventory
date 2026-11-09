# Kindergarten Meal & Inventory Tracker

Backend for a kindergarten kitchen: it tracks food products in stock, meal recipes and every meal served, automatically deducts ingredients from inventory, and produces usage reports in the background.

## Features

- **Auth & roles** — registration, login and token refresh with JWT; roles: `admin`, `manager`, `cook`. Public sign-up creates cook accounts only; admin/manager accounts are created by an admin (the first user of an empty system becomes admin)
- **Products** — CRUD for food products and their stock
- **Meals** — CRUD for meals and their ingredients
- **Serving meals** — `POST /serve-meal` records a serving and deducts ingredients from inventory
- **Portion estimates** — how many portions can still be cooked from current stock
- **Background jobs (Celery + Redis)** — monthly usage report and inventory discrepancy rate, with task status endpoints
- **Reports & logs** — usage report, servings log, notifications
- **Real-time updates** — WebSocket endpoint `/ws/inventory`
- **Tests** — pytest test suite for the API endpoints

## Tech stack

Python · FastAPI · PostgreSQL · SQLAlchemy (async) + asyncpg · Alembic · Celery · Redis · JWT (python-jose) · passlib/bcrypt · Docker Compose · pytest

## Project structure

```
backend/
├── app/
│   ├── main.py          # FastAPI app and endpoints
│   ├── models.py        # SQLAlchemy models
│   ├── schemas.py       # Pydantic schemas
│   ├── crud.py          # database operations
│   ├── auth.py          # password hashing, JWT
│   ├── celery_app.py    # Celery configuration
│   ├── tasks.py         # background tasks (reports)
│   └── reporting.py     # report helpers
├── migrations/          # Alembic migrations
└── tests/               # pytest tests
docker-compose.yml       # backend, PostgreSQL, Redis, Celery worker
```

## Getting started

### With Docker

```bash
cp .env.example .env      # fill in your own values
docker compose up --build
```

API: http://localhost:8000 · Swagger docs: http://localhost:8000/docs

### Locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # fill in your own values
cd backend
alembic upgrade head
uvicorn app.main:app --reload
celery -A app.celery_app worker --loglevel=info   # in a second terminal
```

### Tests

```bash
cd backend
pytest
```

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | Secret used to sign JWT tokens |
| `REDIS_URL` | Redis connection string (Celery broker + WebSocket pub/sub) |
