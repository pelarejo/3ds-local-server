FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/3ls/app

RUN pip install "pipenv==2026.5.2"
COPY Pipfile Pipfile.lock ./
RUN pipenv install --system --deploy

COPY --chown=10001:10001 . .
RUN chmod 0755 /srv/3ls/app/entrypoint.sh \
    && mkdir -p /srv/3ls/content /srv/3ls/system /srv/3ls/data \
    && chown -R 10001:10001 /srv/3ls

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/srv/3ls/app/entrypoint.sh"]
