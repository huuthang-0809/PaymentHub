FROM python:3.12-slim

WORKDIR /app

COPY docker/generator-requirements.txt /app/generator-requirements.txt
RUN pip install --no-cache-dir -r /app/generator-requirements.txt

COPY src /app/src
ENV PYTHONPATH=/app/src

CMD ["python", "-m", "synthetic_data.mongo_seed"]
