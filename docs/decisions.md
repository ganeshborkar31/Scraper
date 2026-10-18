# Decisions and unresolved requirements

- Used FastAPI, Pydantic v2, SQLAlchemy 2, Celery, Redis and PostgreSQL per request; SQLite is the developer fallback.
- Started with create_all for a runnable fresh-database slice; versioned Alembic migrations are outstanding.
- Chose HTTP Basic over cookie sessions, avoiding CSRF-prone browser cookies; deploy only behind TLS and configure non-example credentials.
- Implemented deterministic domain then normalized name/city dedupe. Ambiguous fuzzy matching is intentionally omitted.
- No network provider was verified, so OSM/Overpass and registries/search APIs are not enabled. This avoids invented behavior or permissions.
- Dataset local paths are controlled by administrators. User uploads and generic website crawler are not implemented.
- Schedule dispatcher checks every minute; multi-dispatcher locking and complete update semantics remain outstanding.
