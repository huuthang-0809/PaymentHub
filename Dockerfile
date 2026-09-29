FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

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

COPY --chown=app:app pyproject.toml uv.lock ./

RUN uv sync --no-install-project
COPY --chown=app:app src ./src
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src

USER 10001:10001
