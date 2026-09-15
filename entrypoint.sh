#!/bin/sh
set -eu

mkdir -p /srv/3ls/data /srv/3ls/content /srv/3ls/data/static

python manage.py migrate --noinput
python manage.py seed_catalog
python manage.py collectstatic --noinput

exec gunicorn threels_server.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-1}" \
    --access-logfile - \
    --error-logfile -
