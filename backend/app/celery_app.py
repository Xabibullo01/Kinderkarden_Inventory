from celery import Celery
import os

from .config import REDIS_URL

# include=... makes the worker import app.tasks, otherwise it doesn't know any task.
celery_app = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL, include=['app.tasks'])


celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # Eager mode runs tasks inside the API process. Only enable it for local debugging/tests.
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true",
    task_eager_propagates=True,
)