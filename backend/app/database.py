from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy import create_engine
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set")

# Async engine for FastAPI
engine = create_async_engine(DATABASE_URL, echo=True)

# Sync engine for Celery
sync_engine = create_engine(DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2"), echo=True)

# Async session factory
# Async session factory
async_session_factory = async_sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,    # ← add this line
)


# Sync session factory for Celery
sync_session_factory = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=sync_engine,
)

# Declarative base class
class Base(DeclarativeBase):
    pass

# Async database initialization
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Dependency to get async session
async def get_db():
    async with async_session_factory() as session:
        yield session

# Dependency to get sync session for Celery
def get_db_sync():
    db = sync_session_factory()
    try:
        yield db
    finally:
        db.close()