# Scheduled automatic collection

Schedule administration uses the same HTTP Basic credentials as other admin routes. Celery Beat checks persisted schedules once per minute; it does not perform source requests. Eligible schedules are claimed in the database before collection jobs are queued, so multiple Beat dispatches cannot claim the same due time. Collection executes through the existing Celery worker and connector pipeline.

## Create a schedule

```http
POST /schedules
Content-Type: application/json
Authorization: Basic ...
```

```json
{
  "query": "bakery",
  "countries": ["US", "CA"],
  "region": "California",
  "city": "Oakland",
  "category": "bakery",
  "source_ids": ["<source-id>"],
  "interval_minutes": 1440,
  "max_results": 50,
  "source_limits": {"<source-id>": 25},
  "bounds": {"south": 37.70, "west": -122.30, "north": 37.75, "east": -122.25},
  "enabled": true,
  "allow_overlap": false,
  "retry_attempts": 3
}
```

Countries are unique ISO alpha-2 codes (1–20); the schedule creates one job for each country on each execution. Frequency must be between 5 minutes and one year. `max_results` is the total candidate record cap across all sources for each country job (1–1000). `source_limits` maps source IDs to lower per-source caps; connector-enforced limits still apply. When `source_ids` is omitted, the worker selects configured enabled, approved sources that support each country and filter set. Explicit sources are validated when the schedule is created and revalidated before every run. Country, region, city, category, and the same bounded geographic area accepted by `/jobs` are available.

## Pause, inspect, and resume

`GET /schedules` lists configuration and last-run errors. `PATCH /schedules/{id}?enabled=false` pauses future dispatch; setting `enabled=true` resumes it and schedules the next run one full interval later. Already queued/running collection jobs are allowed to finish. `DELETE /schedules/{id}` archives the schedule and stops future dispatch while preserving execution history. `GET /schedules/{id}/runs?limit=50&offset=0` lists execution history, statuses, errors, and child job IDs. Use `GET /jobs/{job-id}` and `/jobs/{job-id}/results` to inspect the per-country collection results.

By default a due run is skipped and recorded as `skipped_overlap` if a prior execution still has queued, running, or retrying jobs. Set `allow_overlap` only when the source permits concurrent requests and the worker capacity is sufficient. The scheduler also honors each source's optional `min_schedule_interval_minutes` configuration (never below five minutes).

Transient timeouts, rate limits, temporary server failures, and temporary unavailability trigger exponential Celery retries, capped by `retry_attempts` (0–5). Authentication, configuration, and malformed-response errors are recorded without retry. Every job uses the normal deterministic source-identifier/matching pipeline, so retrying does not create new canonical businesses for the same source records. Per-source observations and all execution outcomes remain attributable. Review source terms, retention, rate limits, concurrency allowances, and applicable privacy requirements before enabling a recurring schedule; schedules do not grant additional access rights.
