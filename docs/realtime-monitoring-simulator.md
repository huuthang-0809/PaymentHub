# Realtime monitoring simulator

The simulator creates varied transaction lifecycles and publishes every event
to the durable RabbitMQ canonical-event exchange. The normal consumer commits
them to PostgreSQL and updates `window_aggregate`; Grafana never receives data
through a shortcut.

## Start

```bash
docker compose --profile realtime up -d --build \
  rabbitmq-consumer realtime-simulator grafana
```

Open `http://localhost:3000/d/paymenthub-transactions/transaction-monitoring`.
The provisioned dashboard defaults to the latest hour and refreshes every 30
seconds.

## Observe the flow

```bash
docker compose --profile realtime logs -f realtime-simulator rabbitmq-consumer
docker compose exec rabbitmq rabbitmqctl list_queues \
  name messages_ready messages_unacknowledged consumers
```

## Tune the scenario

```bash
REALTIME_TXNS_PER_SECOND=0.5 \
REALTIME_FAILURE_RATE=0.08 \
REALTIME_STUCK_RATE=0.03 \
docker compose --profile realtime up -d --force-recreate realtime-simulator
```

Supported environment variables:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `REALTIME_TXNS_PER_SECOND` | `0.2` | New transactions created each second |
| `REALTIME_FAILURE_RATE` | `0.03` | Terminal transactions ending as failed |
| `REALTIME_STUCK_RATE` | `0.01` | Transactions intentionally left pending |
| `REALTIME_MIN_TERMINAL_DELAY_SECONDS` | `2` | Minimum simulated processing delay |
| `REALTIME_MAX_TERMINAL_DELAY_SECONDS` | `12` | Maximum simulated processing delay |
| `REALTIME_SEED` | `20261013` | Reproducible scenario seed |
| `RABBITMQ_BATCH_SIZE` | `20` | Maximum messages committed per DB batch |
| `RABBITMQ_BATCH_WAIT_SECONDS` | `5` | Maximum time used to fill a batch |
| `MONITORING_REALTIME_LOOKBACK_HOURS` | `24` | History recomputed by the realtime consumer |

Keep the default rate first. Increase it gradually while checking the main
queue. A continuously growing `messages_ready` value means the publisher is
faster than the consumer on the current machine.

## Stop

```bash
docker compose --profile realtime stop realtime-simulator
```

Stopping the simulator does not delete published events, aggregates, queues or
Grafana dashboards.
