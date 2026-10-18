# Job lifecycle and operations

The API stores a job before publishing it to Celery. Collection runs only in workers, through the shared connector, validation, normalization, deduplication, and persistence pipeline. Both manual discovery and scheduled collection enqueue the same `run_job` task. The job row is the durable source of truth; Redis/Celery delivery is treated as at least once.

## Status and retry behavior

Jobs use `queued`, `running`, `retrying`, `succeeded`, `partial_success`, `failed`, `cancel_requested`, and `cancelled`. Worker claims use a database compare-and-set so duplicate Celery deliveries cannot run the same job concurrently. Source outcomes and aggregate counts are stored in `Job.outcome`: discovered, accepted, rejected, inserted, updated, and duplicate counts, along with per-source errors. A source failure does not roll back observations already committed by another source.

Transient timeout, rate-limit, temporary-server, and availability errors receive bounded exponential retries, with `Retry-After` taking precedence over the backoff. Authentication, configuration, validation, and malformed-response failures are not retried. Source connectors own their transport timeouts and configured request spacing. A unique `(collection_job_id, source_id, job_record_key)` constraint makes source observations safe under redelivery; canonical business matching remains in the normal ingestion service.

## API

All routes below require the existing HTTP Basic administrator authentication:

- `POST /jobs` submits a validated discovery request and returns `202` with a job ID.
- `GET /jobs?limit=50&offset=0` lists recent jobs.
- `GET /jobs/{id}` returns lifecycle timestamps, source IDs, retry metadata, errors, and outcome counts.
- `GET /jobs/{id}/results?limit=50&offset=0&disposition=all` lists saved business results.
- `POST /jobs/{id}/cancel` cancels queued work immediately or returns `cancel_requested` for a running worker. A worker acknowledges cancellation at a source boundary; stale cancellation is reconciled after the execution lease expires.

Terminal jobs are not restarted by duplicate queue deliveries. Cancellation is cooperative because a connector request already in progress cannot be interrupted by the job API; source request timeouts bound that wait.

## Recovery and commands

Celery Beat periodically claims due schedules and runs `recover_stale_jobs`. Recovery republishes queued/retrying jobs whose delivery lease expired and recovers running jobs whose maximum execution duration elapsed. The database claim makes dispatch safe if Beat is accidentally started more than once. Schedules with overlap disabled also check for active child jobs.

Start the full stack, including migrations, API, worker, and scheduler:

```sh
docker compose up --build
```

For a host-run development setup, start Redis and set `DATABASE_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`, then run the following in separate terminals:

```sh
uvicorn app.main:app --reload
celery -A app.tasks.celery_app:celery worker --concurrency=1 --loglevel=INFO
celery -A app.tasks.celery_app:celery beat --loglevel=INFO
```

The one-worker recommendation limits shared Overpass access. PostgreSQL/Redis/Compose crash recovery and multi-worker source quota behavior have not been exercised in this environment. Connector operations are bounded by source transport timeouts, but cancellation is not preemptive inside an active network request.
