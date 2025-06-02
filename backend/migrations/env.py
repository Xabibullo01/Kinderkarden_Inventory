import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

# -- .env faylni yuklash --
load_dotenv()

# -- Project root pathni sys.path ga qo‘shish --
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# -- Model import (to'g'rilangan yo‘l) --
from app.models import Base  # Faqat 'app', 'backend.app' emas

# -- Alembic Config obyektini olish --
config = context.config

# -- Logger konfiguratsiyasi --
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# -- Model metadata --
target_metadata = Base.metadata

# -- DATABASE_URL ni sync adapterga moslashtirish --
database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise ValueError("DATABASE_URL is not set in .env file")

# -- asyncpg -> psycopg2 o‘zgartirish (Alembic uchun) --
sync_database_url = database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")
config.set_main_option("sqlalchemy.url", sync_database_url)


def run_migrations_offline() -> None:
    """Offline rejimda migratsiyalarni bajarish."""
    context.configure(
        url=sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online rejimda migratsiyalarni bajarish."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
