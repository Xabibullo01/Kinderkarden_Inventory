"""Central place for settings read from environment variables."""
import os

from dotenv import load_dotenv

load_dotenv()

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INVENTORY_CHANNEL: str = "inventory_updates"
# SQL echo is useful while debugging, but too noisy (and leaks data) in production.
SQL_ECHO: bool = os.getenv("SQL_ECHO", "false").lower() == "true"
