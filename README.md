# Business Data Discovery

A policy-aware MVP for collecting business-level records from reviewed sources. Coverage is limited to configured sources; the application does not grant permission to access, retain, or reuse data.

## Status

Working: FastAPI dashboard/API, HTTP Basic protected admin routes, SQLAlchemy persistence, async Celery job queue, persisted schedules dispatched by Celery Beat, normalized business/source provenance records, explainable domain/name deduplication, bounded local CSV/JSON dataset import, search/pagination, CSV export with formula-injection mitigation, and synthetic fixture.

Only the local dataset connector is implemented. No live external connector is enabled. The seeded source contains synthetic demonstration records only. Overpass, company-registry, commercial search, and allowlisted crawling require policy review and implementation; they are not represented as working integrations.

## Run locally

Python 3.12+ and `uv` (or pip) are required. For Docker Compose, copy `.env.example` to `.env`, change `APP_SECRET_KEY` and `ADMIN_PASSWORD`, then run `docker compose up --build`. Visit http://localhost:8001 and authenticate with the configured Basic Auth credentials. Set `APP_PORT` in `.env` to another free host port if needed. The example password is local-only.

Without Docker: install `uv sync --extra dev` (or `pip install -e '.[dev]'`), set `DATABASE_URL=sqlite:///./business.db`, then run `python -m app.db.init_db`, `uvicorn app.main:app --reload`, and separately start Redis plus `celery -A app.tasks.celery_app:celery worker -B --loglevel=INFO` for jobs and schedule dispatch. The production Compose configuration uses PostgreSQL, Redis, worker, and scheduler containers.

## API

Admin endpoints use HTTP Basic authentication: `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /sources`, `GET /businesses`, `GET /businesses/{id}`, `GET /businesses.csv`, and schedule CRUD endpoints under `/schedules`. `GET /health` and `/ready` are public. Search requests accept query, ISO alpha-2 country, optional city/category/source IDs, and max_results (1–1000). Job submission returns promptly; work runs in Celery.

The included synthetic dataset is loaded at startup. To add an administrator-controlled local file, configure a source in the database with connector `dataset` and an absolute/controlled local path. Do not point it to a user supplied URL.

## Configuration

See `.env.example`: `APP_ENV`, `APP_SECRET_KEY`, `DATABASE_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, and `PUBLIC_CONTACT_EMAIL`. Never use the sample credentials in production. Provider credentials belong in environment/secret storage, never source config.

## Operations and gaps

Compose runs Alembic migrations in a one-shot service before starting the API, worker, or scheduler. `create_all` also bootstraps a direct local run. Back up PostgreSQL with `pg_dump` and restore with `pg_restore`; Redis is queue/schedule infrastructure, while application state is in PostgreSQL. Retention/deletion hooks and administrator source approval CRUD are not yet complete. The minimal schedule API supports create, pause/resume, delete; updates to query/interval and overlap locking need hardening before multi-scheduler production use. Jobs are isolated per source, but rate limiting and worker retries need operational tuning. Basic Auth is intended behind TLS/reverse proxy. See docs for source approval and connector limits.
