FROM python:3.12-slim

WORKDIR /app

COPY docker/requirements/test-data-generator.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN groupadd --system --gid 10001 app \
    && useradd \
        --system \
        --uid 10001 \
        --gid app \
        --home-dir /app \
        --shell /usr/sbin/nologin \
        app \
    && mkdir -p /test-data \
    && chown -R app:app /app /test-data

COPY --chown=app:app src /app/src
COPY --chown=app:app tests/__init__.py /app/tests/__init__.py
COPY --chown=app:app tests/data_generator /app/tests/data_generator
ENV PYTHONPATH=/app/src:/app

USER 10001:10001

CMD ["python", "-m", "tests.data_generator.mongo_seed"]
