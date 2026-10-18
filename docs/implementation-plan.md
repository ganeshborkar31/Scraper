# Implementation plan and checklist

- [x] Foundation: repository instructions, settings, package, container stack, docs.
- [x] Persistence: source, business, provenance, job and schedule models; fresh database bootstrap.
- [x] Ingestion: connector protocol, local CSV/JSON import, synthetic sample, normalization and deterministic upsert.
- [x] Background work: queued jobs, source-level partial failure handling, Celery schedule polling.
- [x] API/UI: protected dashboard, search jobs, source listing, business list/detail, provenance, CSV export, schedule endpoints.
- [x] Initial versioned Alembic migration; full admin source CRUD remains outstanding.
- [ ] Verified live connector, robust domain rate limiting and crawler policy enforcement.
- [ ] Schedule overlap lock, cancellation, per-source outcome UI and complete integration tests.

## Current execution

The first vertical slice uses HTTP Basic protection and local file imports. Live collection remains disabled. SQLite supports a no-Docker development path; Compose config targets PostgreSQL and Redis. Six unit tests currently cover normalization, dataset parsing, authentication guard, and deterministic deduplication; the full requested API/worker/schedule integration matrix remains outstanding.
