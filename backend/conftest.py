"""
conftest.py — sets TESTING=1 so Django settings.py uses SQLite in-memory.
In Docker/CI (with real PostgreSQL), do not use this file.
"""
import os

# MUST be set before Django settings module loads
os.environ["TESTING"] = "1"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def pytest_configure(config):
    from django.conf import settings
    # Celery runs synchronously in tests
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

