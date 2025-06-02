from celery import Celery
from dotenv import load_dotenv
import os

load_dotenv()

celery_app = Celery('tasks', broker='redis://redis:6379/0', backend='redis://redis:6379/0')


celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    task_always_eager=True,
    task_eager_propagates=True,
)