# Implementation plan and checklist

- [x] Foundation: repository instructions, settings, package, container stack, docs.
- [x] Persistence: source, business, provenance, job and schedule models; fresh database bootstrap.
- [x] Ingestion: connector protocol, local CSV/JSON import, synthetic sample, normalization and deterministic upsert.
- [x] Background work: queued jobs, source-level partial failure handling, Celery schedule polling.
- [x] API/UI: protected dashboard, search jobs, source listing, business list/detail, provenance, CSV export, schedule endpoints.
- [x] Initial versioned Alembic migration; full admin source CRUD remains outstanding.
- [x] Extensible connector lifecycle with fixture connector and classified source/record errors.
- [x] Phase 4 OpenStreetMap Overpass integration: bounded live-query connector, provenance, saved fixtures, and documented API collection. Live service availability is not exercised by ordinary tests.
- [ ] Broader live connectors, robust distributed rate limiting, and crawler policy enforcement.
- [ ] Schedule overlap lock, cancellation, per-source outcome UI and complete integration tests.

## Current execution

The current slice uses HTTP Basic protection, local file imports, and a bounded OpenStreetMap Overpass connector. Automated tests use saved Overpass responses; no live request or complete deployed worker flow is verified by the test suite. SQLite supports a no-Docker development path; Compose config targets PostgreSQL and Redis. The full API/worker/schedule integration matrix remains outstanding.
