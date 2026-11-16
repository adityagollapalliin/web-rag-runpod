"""Celery application. Start a worker with:

uv run celery -A app.workers.celery_app worker --loglevel=info
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("docqa", broker=settings.celery_broker_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,  # a job is re-delivered if the worker dies mid-way
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_always_eager=settings.celery_eager,
)
