# Development audit and task list

Audit date: 2026-10-18

This file records repository state after inspection and checks. “Verified” means a local command or test exercised the behavior in this environment. The Docker daemon was not accessible, so Compose startup and PostgreSQL behavior remain unverified.

## Completed and verified

- [x] Phase 5 normalization/deduplication implemented: normalized business keys, preserved raw source fields, quality-ranked canonical updates, exact/likely/uncertain/distinct decisions, conservative matching, and audited reversible merges.
- [x] Phase 5 verification: `pytest -q` passes (31 tests), Ruff passes for `app`, `tests`, and `alembic`, and Alembic upgrades both a clean database and an existing Phase 4 SQLite schema through `0004_matching_audit`.

- [x] Phase 4 OpenStreetMap Overpass connector implemented with bounded bbox/category queries, OSM attribution/provenance, validation, saved-response tests, and API documentation.
- [x] Phase 4 verification: `pytest -q` passes (21 tests), Ruff passes for `app`, `tests`, and `alembic`, and Alembic upgrades a clean SQLite database through `0003_job_bounds`.

- [x] Phase 3 connector framework implemented: common connector lifecycle, transport-specific hooks, classified errors, test fixture connector, job integration, provenance, and connector authoring docs.
- [x] Phase 3 verification: `pytest -q` passes (13 tests) and Ruff passes for `app`, `tests`, and `alembic`.

- [x] Phase 2 database foundation implemented: extended business model, attributable source observation records, incremental migration, and tests for create/update/constraints/duplicate handling/migration execution.
- [x] Phase 2 verification: `pytest -q` passes (11 tests), Ruff passes for `app`, `tests`, and `alembic`, and Alembic upgrades a clean SQLite database through `0002_business_provenance`.

- [x] FastAPI application imports successfully with the installed environment.
- [x] Unit suite passes: 6 tests cover helper-level authentication rejection, health helper output, basic normalization, synthetic dataset filtering/labels, repeat source external-ID ingestion, domain matching, and separation of similar names.
- [x] Ruff reports no findings for `app` and `tests`.
- [x] Alembic applies the current initial revision to a clean SQLite database.
- [x] `docker compose config --quiet` validates the current Compose file.
- [x] Uvicorn reached “Application startup complete” against a temporary SQLite database. HTTP requests and the complete worker/Redis flow were not verified.
- [x] `.env` is ignored by Git and is not tracked. `.env.example` contains example values, not production credentials.

## Current components

- **Architecture:** FastAPI request layer, SQLAlchemy persistence, Celery worker and Beat scheduler, Redis queue, PostgreSQL Compose service, SQLite developer fallback. Dataset and bounded OpenStreetMap Overpass connectors are registered; the in-memory fixture connector is for offline tests. Matching decisions and reversible merge events are persisted.
- **Backend and UI:** API and a minimal HTML dashboard are present. There is no separate frontend. Dashboard job content is interpolated into HTML without escaping; see known issues.
- **Database:** Source, business, provenance, match decision, merge audit, job, and schedule models exist. Migrations `0002` through `0004` expand prior schemas; `0001_initial` still builds from live metadata and should be made a stable historical migration. Repeat observations and raw candidate field values remain source-attributed.
- **API:** Health/readiness, job submission/status, sources listing, business search/detail/export, and basic schedule CRUD routes exist. API tests do not exercise routes through an HTTP client.
- **Connectors:** The common lifecycle supports independent discovery/fetch/parse/normalization and pagination strategies; configured request spacing and classified errors are available. Local CSV/JSON and fixture connectors plus a bounded OpenStreetMap Overpass connector are implemented. Saved-response tests do not make live requests. There are no other provider connectors, allowlisted crawler, file upload, fetch/enrich operation, or advanced distributed quota manager.
- **Background work:** Celery job execution and periodic schedule dispatch code exists. No tests cover worker transitions, partial failures, retry behavior, schedule dispatch, overlap prevention, or idempotency across whole jobs.
- **Authentication:** HTTP Basic protects most admin routes. Production startup rejects a short/default key and the sample admin password, but accepts the `.env.example` secret placeholder if the admin password is changed. No CSRF defense is present for browser form submission; review Basic Auth browser behavior and add an appropriate defense before wider deployment.
- **Configuration and containers:** Compose configuration parses and defines a one-shot migration service plus PostgreSQL/Redis/API/worker/scheduler. Container startup was not run because Docker daemon access was denied. PostgreSQL credentials are fixed development values in Compose. The local README flow does not override its Compose-oriented Redis hostname for a host-run worker.
- **Documentation:** README, architecture, decision, source review, connector, and implementation-plan documents exist. Some documentation overstates completion (for example, the plan calls several broad slices complete despite minimal UI and missing integration tests); align it with this audit.

## Known issues and missing work

- [ ] **Security, high priority:** Escape `Job.query` and other user-controlled values rendered in the dashboard. Add a regression test for HTML injection.
- [ ] **Production configuration:** Reject placeholder/example `APP_SECRET_KEY` values and require explicit non-example credentials. Move PostgreSQL credentials out of Compose literals or clearly constrain them to local development.
- [ ] **Migration safety:** Replace metadata-driven initial migration with explicit schema operations and add incremental revisions. Decide whether runtime `create_all` remains only a development helper.
- [ ] **API coverage:** Add HTTP-level tests for auth, validation, pagination, CSV formula escaping, job queue failure, and protected source/schedule actions. Current tests call helper functions directly and do not establish route protection comprehensively.
- [ ] **Source correctness:** Filter approved/enabled sources by country and record type; validate requested source IDs against compatible sources. Add admin source review/enable/disable endpoints with auditability.
- [ ] **Data ingestion:** Add broader worker transaction/error coverage and protections for unbounded dataset files/row counts. Field updates now use configured quality ranks, and every observation has a persisted match decision.
- [ ] **Schedules:** Add atomic/transactional overlap protection keyed to schedule ID, robust schedule updates, location/category/source compatibility, and dispatch/retry tests. Existing duplicate check is a read-before-write query and can race between dispatchers.
- [ ] **Operations:** Add structured redacted logs, per-source timeout/rate-limit controls, worker health/readiness, cancellation behavior, metrics, and retention/deletion hooks.
- [ ] **Product requirements:** Add city/category/source filters, usable business and job detail pages, schedules page, and visible attribution. Source detail/health and latest-error overview are limited.
- [ ] **Integrations:** Official registry APIs, commercial search APIs, and website crawling are absent. Keep them explicitly unavailable until current technical and policy requirements are verified; do not present the synthetic fixture as live coverage.
- [ ] **Local workflow:** Document/run host Redis configuration (`localhost` URLs) or provide a no-Docker Compose profile that starts Redis. Verify the complete documented workflow with a real Redis service.
- [ ] **PostgreSQL/Compose:** Run a clean-volume migration and end-to-end job in Docker when daemon access is available. Confirm migration completion gates worker startup and that persisted schedule queries see the migrated schema.
- [ ] **Docs consistency:** Update `docs/implementation-plan.md` and `docs/decisions.md` to distinguish implemented, tested, untested, and outstanding work.

## Recommended implementation order and dependencies

1. **Security and configuration baseline** — HTML escaping, tests, strict production secret validation, and local-only Compose credentials. This should precede exposing the dashboard beyond a trusted development machine.
2. **Persistence reliability** — explicit Alembic revisions and clean PostgreSQL migration verification. Required before relying on upgrades or deployment data.
3. **API and ingestion test seam** — HTTP-level auth/validation/export tests, injectable queue/connector boundaries, and tests for source compatibility, partial failure, and idempotent updates.
4. **Job and schedule reliability** — transactional enqueue/dispatch, per-schedule overlap locking, retries, and schedule CRUD semantics. Depends on persistence reliability.
5. **MVP UI completion and operations** — usable detail/list pages, attribution, structured logs, health/metrics, retention hooks, rate limits. Depends on stable API and job semantics.
6. **Reviewed external connector** — only after official documentation, permitted usage, limits, credentials, and retention are verified. Connector tests should use offline fixtures.

## Smallest next milestone

Implement and test the **security/configuration baseline**: escape dashboard-rendered job text, reject placeholder production secrets, and add focused regression tests. This is a small isolated change with clear pass/fail checks and addresses the most immediate exposure risk before expanding features.
