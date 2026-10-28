# Case event stream: outbox → Kafka

Publishes every case state change to Kafka after it is committed, so other services (Monitor, notifications, analytics, cost meter) can react without touching the request path or querying the app database. The underwriting flow does not change and does not depend on Kafka.

## Today

- `UnderwritingService._event()` appends an event to `case.events` on every transition: create, assessment, statement, CIC inquiry, decision, handoff attempt and result.
- `PostgresCaseRepository._save()` writes it to `case_events` in the same transaction as the case. The table is append-only (`events_immutable` trigger, NFR-05).
- `api/messaging/` has `KafkaClient` and `MemoryClient`, but nothing in the app publishes.

## Why an outbox, not publish in `_event()`

Publishing inside `_event()` or the transaction breaks either way:

- publish before commit: Kafka gets events from transactions that later roll back (version conflict, policy error)
- publish after commit: a crash or Kafka error in between loses the event for good

`case_events` itself cannot be the queue. It has no global order column, so "read everything after the last id" can skip a row that commits late, and the immutability trigger forbids marking rows as sent.

So: a separate `event_outbox` table, filled in the same transaction as `case_events`, and a relay that drains it.

```
request ─▶ UnderwritingService ─▶ repository.transaction()
                                     ├─ case_events  (append-only, audit)
                                     └─ event_outbox (same commit)
                                              │
                           EventRelay (thread) │ every second
                                              ▼
                         Kafka riverbank.case-events, key = case_id
                                              ▼
                         consumers: Monitor, notifications, analytics
```

## Outbox table (`db/migrations/005_event_outbox.sql`)

```sql
create table event_outbox (
    event_id     uuid primary key references case_events(id),
    case_id      uuid not null references cases(case_id),
    ordinal      integer not null,
    payload      jsonb not null,
    created_at   timestamptz not null default clock_timestamp(),
    published_at timestamptz
);
create index event_outbox_pending on event_outbox (created_at) where published_at is null;
```

- `_save()` inserts into `event_outbox` only when the `case_events` insert actually adds a row (`on conflict do nothing returning id`). Re-saving a case does not queue its old events again.
- `clock_timestamp()` instead of `now()`: two transactions on the same case are serialised by `FOR UPDATE`, and the second one inserts after the first commits, so per-case order by `created_at` is safe even if the second transaction started earlier.
- Events written before this migration are not backfilled.

## Relay (`api/services/events.py`)

`EventRelay.run_once()`:

1. `repository.publishing(batch_size)` opens one transaction and takes `pg_try_advisory_xact_lock`. If another process holds it, return nothing. One relay at a time keeps per-case order across uvicorn workers.
2. Read pending rows ordered by `created_at, case_id, ordinal`.
3. Publish each with key `case_id`, then `flush()`.
4. Mark the rows `published_at`, commit.

If publish or flush fails, the transaction rolls back, rows stay pending, and the relay retries after `retry_seconds`. Kafka being down never affects underwriting; events pile up in the outbox and drain when it is back.

`start_relay()` runs the loop in a daemon thread from the FastAPI lifespan, the same way `start_worker()` runs the job worker. Off unless `RIVERBANK_EVENT_RELAY=on`. With `RIVERBANK_MESSAGE_BUS=memory` it refuses to start and logs an error, because the memory bus would mark every event published while nothing leaves the process.

The same loop deletes published rows older than `RIVERBANK_EVENT_OUTBOX_RETENTION_HOURS` (default 336, matching topic retention) once every `RIVERBANK_EVENT_OUTBOX_PRUNE_SECONDS`. Pending rows are never deleted.

`MemoryCaseRepository` keeps an in-process outbox with the same contract, for tests and local runs.

## Observing

| What | Where |
|---|---|
| Messages on the topic, per partition, key and JSON value | Redpanda Console at `https://<SITE_ADDRESS>/kafka`, same basic auth as `/db` |
| Each event's delivery state | pgweb, view `event_outbox_status`: `PENDING`, `FAILING` (with `attempts`, `last_error`, `last_attempt_at`), `PUBLISHED` |
| Relay failures over time | CloudWatch `/riverbank/app`, log line `event relay failed` |

A failed batch rolls back, then the relay adds one to `attempts` and stores the error on every row of that batch in a separate transaction. Rows keep their `attempts` after they are finally published, so `PUBLISHED` with `attempts > 0` means "delivered after a retry".

```sql
select status, count(*), max(attempts), min(created_at) as oldest
from event_outbox_status group by status;

select case_id, reason, attempts, last_error, last_attempt_at
from event_outbox_status where status = 'FAILING' order by created_at limit 20;
```

The console (`kafka-console`, Go, about 30 MiB) can also produce and delete records. It sits behind the same basic auth as pgweb; give that login only to the team.

Consumer-side errors (a consumer that cannot process a message) belong in that consumer's `riverbank.<consumer>.dlq`, see Topic design. No consumer exists yet.

## Message

Topic `riverbank.case-events`, key `case_id`, value JSON:

```json
{
  "schema": "case-event/v1",
  "case_id": "…",
  "ordinal": 3,
  "event_id": "…",
  "event_type": "STATE_CHANGED",
  "actor": "ORCHESTRATOR",
  "previous_status": "DRAFT",
  "next_status": "PENDING_REVIEW",
  "reason": "ASSESSMENT_COMPLETED",
  "occurred_at": "2026-10-22T00:00:00+00:00",
  "evidence_ids": ["…"]
}
```

No plan, features, amounts or seller text. Consumers that need more read the case through the API with their own credentials.

## Delivery guarantee

At least once. A flush can fail after some messages reached the broker; the retry sends them again. Consumers dedupe on `event_id`. Per-case order holds because one relay publishes at a time and the key pins a case to one partition.

## Topic design

### Topics

| Topic | Status | Producer | Key | Consumers |
|---|---|---|---|---|
| `riverbank.case-events` | built (`EventRelay`) | API via outbox | `case_id` | Monitor, notifications, analytics (not built) |
| `riverbank.<consumer>.dlq` | with the first consumer | that consumer | `case_id` | humans, inspect and replay |
| `riverbank.los-handoff` | only with a `KafkaLosAdapter` | API | `case_id` | LOS |
| `riverbank.los-result` | same | LOS | `case_id` | API, stores `los_ref` |

Naming: `riverbank.<thing>-<events|handoff|result>`, lowercase, dots and hyphens only, never underscores (Kafka metric names treat `.` and `_` as the same). `events` are facts for anyone; `handoff` asks one system to act; `result` is its reply. Versions go in the payload, not the name.

No topic for statement uploads or model calls. The Postgres job queue and `ModelGate` handle those (leases, retry, status, concurrency caps).

### `riverbank.case-events`

| Setting | Local | Prod | Why |
|---|---|---|---|
| Partitions | 6 | 6 (or 12) | Up to 6 consumers per group. Adding partitions later remaps `case_id` and breaks order for open cases, so size it once |
| Replication factor | 1 | 3 | One broker can fail without data loss |
| `min.insync.replicas` | 1 | 2 | With `acks=all`, a write needs two copies |
| `retention.ms` | 14 days | 14 days | Time for a consumer to be down and catch up. The permanent record is `case_events` in Postgres |
| `cleanup.policy` | delete | delete | Not compacted: Kafka is not a state store |
| Auto-create | on (dev convenience) | off | An auto-created topic gets 1 partition |

All event types share one topic so a case's whole history (created → assessed → statement → decided → handoff) sits on one partition in order. Key `case_id`, not `seller_id`: order matters per case, and one busy seller would make a hot partition.

Producer settings are already `acks=all`, `enable_idempotence=True` (`messaging/config.py`).

### Consumer contract

1. Dedupe on `event_id` (at-least-once).
2. Order inside a case by `ordinal`; skip an `ordinal` already processed.
3. Ignore unknown `event_type` values and unknown fields.
4. One consumer group per service: `riverbank-monitor`, `riverbank-notify`, `riverbank-analytics`.
5. Commit offsets only after processing (`enable_auto_commit=False` is already the client default).
6. A message that keeps failing goes to `riverbank.<consumer>.dlq` with the error, so it does not block the partition.

Schema changes: adding a field stays `case-event/v1`. Removing or renaming a field is `case-event/v2` on the same topic, and consumers read both during the switch. JSON with a `schema` field, no registry, while there is one producer.

### Security (prod)

`SASL_SSL` with `SCRAM-SHA-512` (config supports it, credentials from the `riverbank/kafka` secret). ACLs: the API principal may only write `riverbank.case-events`; each consumer may read it with its own group only.

### Creating topics

Local: `make kafka-up` runs `make kafka-topics`, which creates `riverbank.case-events` with 6 partitions and 14-day retention if it is missing. It does not change an existing topic. To fix one that was auto-created with 1 partition:

```bash
docker exec rb-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
  --alter --topic riverbank.case-events --partitions 6
docker exec rb-kafka /opt/kafka/bin/kafka-configs.sh --bootstrap-server localhost:9092 \
  --alter --entity-type topics --entity-name riverbank.case-events --add-config retention.ms=1209600000
```

Prod: `auto.create.topics.enable=false` on the brokers, and create topics from a script or infrastructure config with RF 3 and `min.insync.replicas=2` before turning the relay on.

## Config

| Env | Default | Meaning |
|---|---|---|
| `RIVERBANK_EVENT_RELAY` | `off` | `on` starts the relay thread |
| `RIVERBANK_MESSAGE_BUS` | `memory` | `kafka` to actually leave the process |
| `RIVERBANK_CASE_EVENTS_TOPIC` | `riverbank.case-events` | topic name |
| `RIVERBANK_EVENT_RELAY_BATCH_SIZE` | 100 | rows per transaction |
| `RIVERBANK_EVENT_RELAY_IDLE_SECONDS` | 1 | wait when the outbox is drained |
| `RIVERBANK_EVENT_RELAY_RETRY_SECONDS` | 10 | wait after a failure |
| `RIVERBANK_EVENT_OUTBOX_RETENTION_HOURS` | 336 | published rows older than this are deleted |
| `RIVERBANK_EVENT_OUTBOX_PRUNE_SECONDS` | 3600 | how often the relay prunes |

Local: `make kafka-up` (starts the broker and creates the topic), then `RIVERBANK_MESSAGE_BUS=kafka RIVERBANK_EVENT_RELAY=on`. Prod (`infra/docker-compose.yml`, profile `events`): one KRaft broker `kafka` (heap 256 MB, container limit 640 MB, 128 MB per partition, no published port), a one-shot `kafka-topics` that creates the topic with 6 partitions on every deploy, and `valkey` (no persistence, 48 MB, no published port). `infra/.env.prod` turns it on with `COMPOSE_PROFILES=events`, `RIVERBANK_MESSAGE_BUS=kafka`, `RIVERBANK_EVENT_RELAY=on`, `RIVERBANK_KV_STORE=valkey`. Single broker means RF 1: a lost EBS volume loses the stream, the permanent record stays in `case_events`. The api does not depend on either container to start: Kafka down leaves events in the outbox, Valkey down makes ModelGate fail open.

## Tests (`api/tests/test_events.py`)

- memory outbox: new events queued once, re-save does not duplicate, failed batch stays pending
- relay: publishes in order with key `case_id`, marks published, second run sends nothing; bus failure leaves rows for the retry
- end to end: `POST /v1/cases` through the real service, relay puts `STATE_CHANGED` on the memory bus
- message carries no plan or feature data
- `start_relay()` returns `None` when off
- Postgres: queued with the case, re-save does not duplicate, advisory lock lets one relay through (opt-in, `WORKFLOW_TEST_DATABASE_URL`)

## Not in scope

- Consumers (Monitor, notifications). This only produces the stream.
- `KafkaLosAdapter` for the handoff.
- Schema registry. `schema` field versions the payload.
