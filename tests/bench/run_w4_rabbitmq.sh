#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="paymenthub-w4-rabbit-it"
COMPOSE=(docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.week4.yml)
READY="tests/bench/.w4-kill-ready"
RESUME="tests/bench/.w4-kill-resume"

cd "$ROOT_DIR"
cleanup() { rm -f "$READY" "$RESUME"; }
trap cleanup EXIT
cleanup

"${COMPOSE[@]}" up -d --build postgres rabbitmq migrate rabbitmq-consumer
"${COMPOSE[@]}" stop rabbitmq-consumer
docker exec "$PROJECT-postgres-1" psql -U paymenthub -d paymenthub \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
"${COMPOSE[@]}" run --rm migrate
docker exec "$PROJECT-rabbitmq-1" rabbitmqctl purge_queue payment.monitoring.ingest
docker exec "$PROJECT-rabbitmq-1" rabbitmqctl purge_queue payment.monitoring.dlq
"${COMPOSE[@]}" start rabbitmq-consumer

docker run --rm --name paymenthub-w4-producer \
  --network "$PROJECT"_default \
  -v "$ROOT_DIR:/workspace" -w /workspace \
  -e PYTHONPATH=/workspace/src:/workspace \
  -e RABBITMQ_URL=amqp://paymenthub:paymenthub@rabbitmq:5672/ \
  -e W4_PAUSE_AT_KILL=1 \
  "$PROJECT"-migrate:latest \
  python tests/bench/run_w4_rabbitmq.py produce &
producer_pid=$!

for _ in $(seq 1 300); do
  [[ -f "$READY" ]] && break
  sleep 0.1
done
[[ -f "$READY" ]] || { echo "producer did not reach 40%" >&2; exit 1; }

"${COMPOSE[@]}" kill rabbitmq-consumer
touch "$RESUME"
wait "$producer_pid"
"${COMPOSE[@]}" start rabbitmq-consumer

docker run --rm --network "$PROJECT"_default \
  -v "$ROOT_DIR:/workspace" -w /workspace \
  -e PYTHONPATH=/workspace/src:/workspace \
  -e DATABASE_URL=postgresql://paymenthub:paymenthub@postgres:5432/paymenthub \
  -e W4_EXPECTED_CONFIRMED=25 \
  "$PROJECT"-migrate:latest \
  python tests/bench/run_w4_rabbitmq.py verify
