FROM python:3.12-slim

WORKDIR /app

COPY docker/generator-requirements.txt /app/generator-requirements.txt
RUN pip install --no-cache-dir -r /app/generator-requirements.txt

RUN groupadd --system --gid 10001 app \
    && useradd \
        --system \
        --uid 10001 \
        --gid app \
        --home-dir /app \
        --shell /usr/sbin/nologin \
        app \
    && mkdir -p /output \
    && chown -R app:app /app /output

COPY --chown=app:app src /app/src

ENV PYTHONPATH=/app/src

USER 10001:10001

CMD ["python", "-m", "synthetic_data.mongo_seed"]
