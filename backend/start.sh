#!/bin/bash

# Exit on error
set -e

echo "Starting Migrations..."
python manage.py migrate --noinput

echo "Collecting Static Files..."
python manage.py collectstatic --noinput

echo "Starting Celery Worker..."
celery -A config worker --loglevel=info --concurrency=1 &

echo "Starting Celery Beat..."
celery -A config beat --loglevel=info &

echo "Starting Gunicorn Server..."
# Using exec to make Gunicorn process 1 (handles signals)
exec gunicorn config.wsgi:application --bind 0.0.0.0:8080 --workers 2 --timeout 120 --log-level debug
