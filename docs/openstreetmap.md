# OpenStreetMap test collection

The initial real source uses the read-only Overpass API at
`https://overpass-api.de/api/interpreter`. This connector sends one Overpass QL
request for an explicit bounding box and one allowlisted business category. The
query declares a 25-second server timeout, a 512 MiB memory limit, and a maximum
of 100 output elements. Requests from this connector are serialized in-process
with at least 10 seconds between requests. HTTP 429 responses preserve
`Retry-After`; timeouts, authentication failures, invalid responses, and server
errors have distinct connector error kinds.

## Run a small test collection

Start the documented API, Redis, and Celery worker. Authenticate with the local
admin credentials. First retrieve the source ID:

```sh
curl -u admin:YOUR_ADMIN_PASSWORD http://localhost:8001/sources
```

Then replace `OSM_SOURCE_ID` with the returned ID for
`openstreetmap-overpass`. This example requests bakeries in a small area around
San Francisco; use an area you have a legitimate reason to inspect:

```sh
curl -u admin:YOUR_ADMIN_PASSWORD \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "bakery",
    "country": "US",
    "category": "bakery",
    "source_ids": ["OSM_SOURCE_ID"],
    "max_results": 20,
    "bounds": {
      "south": 37.77,
      "west": -122.43,
      "north": 37.78,
      "east": -122.42
    }
  }' \
  http://localhost:8001/jobs
```

Poll `GET /jobs/{id}` for completion. A mixed result with valid objects and
invalid records is reported as `partial_success`; validation issues appear in
`outcome.sources.openstreetmap.errors`. Browse saved records through
`GET /businesses`; their detail includes OSM attribution and source URLs.

Supported categories are `bakery`, `cafe`, `clothes`, `convenience`, `hardware`,
`pharmacy`, `restaurant`, and `supermarket`. The connector uses OSM tags to
select results; it does not infer category matches from names or descriptions.
The requested country is stored as the record country because Overpass results
do not reliably carry an ISO country code. City, postal code, and street address
are populated only when corresponding address tags exist. Website and telephone
are often absent. Way/relation coordinates use Overpass's bounding-box center,
which is an approximate location rather than a surveyed entrance.

## Usage and licensing limits

The area dimensions are capped at 0.1 degrees in either direction, and each
request returns at most 100 elements. This is for small, manually requested
collections, not systematic or recurring area harvesting. For larger or regular
extracts, use an appropriate OSM extract or a separately reviewed/self-hosted
service. Keep the identifying `OSM_USER_AGENT`, do not raise the fixed request
limits without review, and stop/retry later when Overpass returns a rate limit
or temporary failure. The in-process request gap does not coordinate multiple
independent deployments, so operate one worker instance for this connector.

OpenStreetMap data is under ODbL 1.0. Stored source observations retain the OSM
element type/ID, direct OpenStreetMap URL, source name, and
`© OpenStreetMap contributors, ODbL 1.0` attribution. Display attribution with
publicly used results and assess share-alike obligations before distributing a
derived database. Do not send personal or confidential query data to OSM
services. Follow the [Overpass API manual](https://dev.overpass-api.de/overpass-doc/en/),
[Overpass resource guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html),
and [OpenStreetMap copyright and attribution terms](https://www.openstreetmap.org/copyright).
