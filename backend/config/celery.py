import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("playto")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "retry-stuck-payouts": {
        "task": "payouts.tasks.retry_stuck_payouts",
        "schedule": 15.0,  # every 15 seconds
    },
    "cleanup-expired-idem-keys": {
        "task": "payouts.tasks.cleanup_expired_idempotency_keys",
        "schedule": crontab(minute=0),  # hourly
    },
}
