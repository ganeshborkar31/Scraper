# Web dashboard

The dashboard is served by FastAPI from `app/static/` and uses plain HTML, CSS, and JavaScript. It does not bundle a second frontend framework or ship production mock records. The API and dashboard share the existing HTTP Basic administrator authentication. Static assets contain no private data; protected dashboard and data routes require the configured administrator credentials.

## Sections and data

- **Overview** reads `/dashboard/overview` for persisted business/source counts, job status counts, recent failures, jobs, and source-record activity.
- **Business Explorer** reads `/businesses` with keyword, country, region, city, category, source, and pagination filters. Selecting a result reads `/businesses/{id}` for source provenance, attribution, source URLs, original source fields, and match decisions.
- **Sources** reads `/sources` for approval/enabled status, license, attribution, last run, and connector-specific safe configuration. `PATCH /sources/{id}` supports enable/disable; enabling requires approval and valid connector configuration. Secret values and local dataset paths are not returned or editable in the dashboard.
- **Jobs** reads `/jobs`, `/jobs/{id}`, and `/jobs/{id}/results`; cancellation uses `POST /jobs/{id}/cancel`.
- **Automatic Collection** reads `/schedules`, creates schedules through `POST /schedules`, pauses/resumes with `PATCH /schedules/{id}?enabled=...`, archives through `DELETE /schedules/{id}`, and displays persisted runs from `/schedules/{id}/runs`.

The dashboard displays loading, empty, success, and API error states. Values inserted into the page use DOM text nodes, and source links are restricted to HTTP(S). Forms inherit backend validation for source compatibility, geographic bounds, record caps, and schedule frequency. A source being visible or enabled does not grant collection rights; review policy, attribution, retention, and rate limits before enabling it.

## Development and startup

For local development, install the project and start the API:

```sh
uv sync --extra dev
DATABASE_URL=sqlite:///./business.db python -m app.db.init_db
DATABASE_URL=sqlite:///./business.db uvicorn app.main:app --reload
```

Collection requires Redis and the worker/scheduler processes described in [job reliability](job-reliability.md). To start the complete stack, copy `.env.example` to `.env`, replace the local credentials, then run:

```sh
docker compose up --build
```

The dashboard is available at `http://localhost:8001/` with the administrator credentials from `.env`. Frontend source files are included in the application image by the existing `COPY app ./app` Docker build step.
