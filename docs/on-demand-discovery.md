# On-demand business discovery

All discovery endpoints require HTTP Basic authentication. A request is validated by `SearchRequest`, then checked against enabled, approved business sources and each connector's filter capabilities. The API submits only compatible sources to the existing Celery collection job pipeline. A selected source that is unavailable or cannot apply a requested filter returns HTTP 422 with its reason; when sources are implicit, supported and unsupported sources are reported in the accepted response. A request with no compatible sources returns HTTP 422 and creates no job.

## Submit a search

```http
POST /jobs
Content-Type: application/json
Authorization: Basic ...
```

```json
{
  "query": "bakery",
  "country": "US",
  "region": "California",
  "city": "Oakland",
  "category": "bakery",
  "max_results": 25,
  "source_ids": ["<source-id>"]
}
```

`query` is a required keyword (1–200 characters); country is an ISO alpha-2 code. Region, city, and category are optional, and text filters cannot be blank. `max_results` is 1–1000; source selection is optional and accepts at most 20 unique IDs. If automatic selection finds more than 20 compatible sources, explicitly select up to 20. `bounds`, when supplied, must fit inside a 0.1 by 0.1 degree area. OpenStreetMap requires bounds and one of its configured categories; it applies keyword, city, and region filters to returned OSM tags. The local dataset connector applies keyword, country, region, city, and category filters but does not support bounds. Source coverage and available fields depend on the configured connectors.

Example accepted response:

```json
{
  "id": "<job-id>",
  "status": "queued",
  "selected_sources": [{"id": "<source-id>", "code": "openstreetmap-overpass"}],
  "unsupported_sources": []
}
```

Poll `GET /jobs/{job-id}` for `queued`, `running`, `retrying`, `succeeded`, `partial_success`, `failed`, `cancel_requested`, or `cancelled` status and per-source errors/counts. `POST /jobs/{job-id}/cancel` requests cancellation; a running worker acknowledges after its active connector operation finishes. `GET /jobs/{job-id}/results?limit=50&offset=0&disposition=all` returns deduplicated businesses observed for that job. `disposition` accepts `all`, `new`, or `existing`; existing means the collection matched a canonical business already present or created earlier in that same job. Each result includes source IDs/codes and source URLs. Pagination limits are 1–200 and offsets must be nonnegative. Partial results are visible while a job runs and after source failures.

All accepted records pass through connector normalization, Candidate validation, conservative matching, canonical field quality updates, provenance storage, and deduplication. Malformed records and connector failures are reported in the job outcome. Repeating a collection reuses exact source identifiers and matching canonical businesses; uncertain matches remain separate for review. The API is protected by the existing administrator Basic Auth and should be exposed only behind TLS with restricted credentials.

OSM collection remains subject to its [source policy and field limitations](openstreetmap.md). A supplied country is request context for OSM because bounding-box queries do not independently verify country membership; OSM address fields are often absent. Do not use this connector to circumvent source access controls, retention limits, or attribution requirements.
