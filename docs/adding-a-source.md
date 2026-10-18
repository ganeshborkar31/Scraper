# Adding a source connector

Connectors live in `app/connectors` and implement `app.connectors.base.Connector`:

- `validate(config)` checks required non-secret source settings before a job runs.
- `health_check(config)` is a lightweight availability/configuration check. Do not
  call it on every record; keep any network check bounded and within source terms.
- `collect(request, config)` returns a `ConnectorBatch`. The collection pipeline
  handles persistence and provenance and does not depend on the transport.

For a connector that has discoverable references or pages, subclass
`BaseConnector`. Implement `discover(request, config)`, then override `fetch`,
`parse`, `normalize`, or `pages` where the source needs distinct behavior. API
connectors can implement cursor or page-token pagination and API authentication;
website connectors can implement approved page discovery and HTML parsing. A
source need not use HTTP at all, as shown by the local dataset adapter. Return
normalized `Candidate` Pydantic models. The base class reports malformed records
as `ConnectorIssue` entries while preserving valid records from the same batch.

Raise `ConnectorError` with an appropriate `ConnectorErrorKind` for
`TIMEOUT`, `RATE_LIMITED`, `AUTHENTICATION`, `INVALID_RESPONSE`,
`MALFORMED_RECORD`, `TEMPORARY_SERVER`, `CONFIGURATION`, or `UNAVAILABLE`.
Set `retry_after` for a source-provided rate-limit delay. Map transport library
exceptions at the connector boundary; never include credentials or raw response
bodies in error messages. Collection outcomes preserve the error kind and
message per source. A source failure is isolated from other sources in the job.

Use `request_interval_seconds` only when it matches the reviewed source limit;
for more complex quotas, implement an appropriate per-source limiter in the
connector. Configure approved timeouts, pagination bounds, access method, and
retention behavior in source-specific config. Credentials belong in environment
or secret storage, not `Source.config`.

Register a connector by its stable name in `app/connectors/registry.py`. The
central collection pipeline should not need edits when adding a connector.
Create an approved `Source` row with the matching `connector` name, reviewed
documentation and terms URLs, license and attribution, allowed countries, and
source-specific configuration. Keep it disabled/pending until the source review
is complete. Public visibility alone is not permission to collect or reuse data.

Add offline tests for configuration validation, parsing/normalization, pagination,
malformed records, provenance, and classified failures. `FixtureConnector` is
an in-memory, network-free example for pipeline tests; its data is synthetic test
input and it is not registered as a production integration. Never bypass login,
paywalls, CAPTCHAs, bot defenses, or source rate limits. Minimize retained data
and follow the approved terms, access controls, privacy requirements, attribution,
and deletion/retention rules.
