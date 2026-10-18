# Architecture

FastAPI validates and persists requests; Celery workers perform source access. SQLAlchemy models separate canonical businesses from provider/source records and retain attribution metadata without raw payloads. The connector protocol accepts normalized `SearchRequest` and returns validated `Candidate` values. `DatasetConnector` reads only configured local CSV/JSON files. Connector errors are recorded per source and do not discard other sources' results. Domain match (country scoped) precedes exact normalized name plus city matching.

PostgreSQL is the intended deployed store, Redis carries jobs, and Celery Beat checks persisted schedules once per minute. No global crawling or arbitrary user URL fetching is implemented. Admin routes use HTTP Basic and should be served over TLS.
