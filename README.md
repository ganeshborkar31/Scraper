# Business Data Discovery

A policy-aware MVP for collecting business-level records from reviewed sources. Coverage is limited to configured sources; the application does not grant permission to access, retain, or reuse data.

## Status

Working: FastAPI dashboard/API, HTTP Basic protected admin routes, SQLAlchemy persistence, async Celery job queue, persisted schedules dispatched by Celery Beat, normalized business/source provenance records, conservative explainable matching with reversible reviewed merges, bounded local CSV/JSON import, bounded OpenStreetMap Overpass collection, search/pagination, CSV export with formula-injection mitigation, and synthetic fixture.

The extensible connector lifecycle is implemented, including network-free test fixtures, the local dataset connector, and a bounded OpenStreetMap Overpass connector. No other live external connector is enabled. The synthetic dataset contains demonstration records only. See [docs/openstreetmap.md](docs/openstreetmap.md) for collection limits and field caveats. Official registry, commercial search, and allowlisted crawling connectors remain absent. See [docs/adding-a-source.md](docs/adding-a-source.md) to implement another connector.

## Run locally

Python 3.12+ and `uv` (or pip) are required. For Docker Compose, copy `.env.example` to `.env`, change `APP_SECRET_KEY` and `ADMIN_PASSWORD`, then run `docker compose up --build`. Visit http://localhost:8001 and authenticate with the configured Basic Auth credentials. Set `APP_PORT` in `.env` to another free host port if needed. The example password is local-only.

Without Docker: install `uv sync --extra dev` (or `pip install -e '.[dev]'`), set `DATABASE_URL=sqlite:///./business.db`, then run `python -m app.db.init_db`, `uvicorn app.main:app --reload`, and separately start Redis plus `celery -A app.tasks.celery_app:celery worker --concurrency=1 --loglevel=INFO` and the scheduler. Keep one worker process/instance for the shared Overpass endpoint. The production Compose configuration uses PostgreSQL, Redis, worker, and scheduler containers.

## API

Admin endpoints use HTTP Basic authentication: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/results`, `POST /jobs/{id}/cancel`, `GET /sources`, `GET /businesses`, `GET /businesses/{id}`, `GET /businesses.csv`, `POST /businesses/{source_id}/merge/{target_id}`, `POST /merges/{merge_id}/reverse`, and schedule CRUD/history endpoints under `/schedules`. `GET /health` and `/ready` are public. See [docs/on-demand-discovery.md](docs/on-demand-discovery.md) for search filters, source compatibility, job status, and paginated results. See [docs/job-reliability.md](docs/job-reliability.md) for lifecycle, retries, cancellation, recovery, and worker commands. See [docs/scheduled-collection.md](docs/scheduled-collection.md) for recurring collection, pause controls, overlap handling, retries, and execution history. See [docs/normalization-deduplication.md](docs/normalization-deduplication.md) for matching decisions, field quality, and merge reversal.

The included synthetic dataset and OpenStreetMap source are loaded at startup. For a small OSM test collection, get the source ID from `GET /sources`, then submit an authenticated `POST /jobs` with its ID, a supported `category`, `country`, `max_results` (at most 100), and `bounds` (`south`, `west`, `north`, `east`; each dimension at most 0.1 degrees). See [docs/openstreetmap.md](docs/openstreetmap.md) for a complete request and source limitations. The included OSM connector uses Overpass API, not Nominatim or the tile service. To add an administrator-controlled local file, configure a source in the database with connector `dataset` and an absolute/controlled local path. Do not point it to a user supplied URL.

## Configuration

See `.env.example`: `APP_ENV`, `APP_SECRET_KEY`, `DATABASE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `PUBLIC_CONTACT_EMAIL`. Never use the sample credentials in production. Provider credentials belong in environment/secret storage, never source config.

## Operations and gaps

Compose runs Alembic migrations in a one-shot service before starting the API, worker, or scheduler. `create_all` also bootstraps a direct local run. Back up PostgreSQL with `pg_dump` and restore with `pg_restore`; Redis is queue/schedule infrastructure, while application state is in PostgreSQL. Retention/deletion hooks and administrator source approval CRUD are not yet complete. Schedule claims are atomic in the database and worker retries cover transient connector errors; multi-worker live source quotas and deployed Redis/PostgreSQL behavior still require operational verification. Basic Auth is intended behind TLS/reverse proxy. See docs for source approval and connector limits.
