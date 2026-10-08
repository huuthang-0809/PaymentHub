FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app \
    && mkdir -p /statements \
    && chown app:app /statements

COPY --chown=app:app src ./src
COPY --chown=app:app tests/__init__.py ./tests/__init__.py
COPY --chown=app:app tests/data_generator ./tests/data_generator
ENV PYTHONPATH=/app/src:/app
USER 10001:10001

CMD ["uvicorn", "bootstrap.scheduler_api:app", "--host", "0.0.0.0", "--port", "9101", "--workers", "1"]
