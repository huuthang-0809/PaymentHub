FROM python:3.12-slim

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd \
        --system \
        --uid 10001 \
        --gid app \
        --home-dir /app \
        --shell /usr/sbin/nologin \
        app \
    && chown app:app /app

COPY --chown=app:app requirements.txt ./

RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
ENV PYTHONPATH=/app/src

USER 10001:10001
