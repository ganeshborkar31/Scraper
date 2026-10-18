"""Shared connector contracts and errors.

Connectors own their transport and pagination strategy. The collection service
depends only on ``Connector.collect`` and validated ``Candidate`` records.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from app.schemas import Candidate, SearchRequest


class ConnectorErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    INVALID_RESPONSE = "invalid_response"
    MALFORMED_RECORD = "malformed_record"
    TEMPORARY_SERVER = "temporary_server"
    CONFIGURATION = "configuration"
    UNAVAILABLE = "unavailable"


class ConnectorError(Exception):
    def __init__(self, kind: ConnectorErrorKind, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


@dataclass
class ConnectorIssue:
    kind: ConnectorErrorKind
    message: str
    record_index: int | None = None
    retry_after: float | None = None


@dataclass
class ConnectorBatch:
    records: list[Candidate] = field(default_factory=list)
    issues: list[ConnectorIssue] = field(default_factory=list)


class Connector(Protocol):
    """Minimal interface used by collection jobs; source transport is pluggable."""

    def validate(self, config: dict[str, Any]) -> tuple[bool, str | None]: ...

    def health_check(self, config: dict[str, Any]) -> bool: ...

    def collect(self, request: SearchRequest, config: dict[str, Any]) -> ConnectorBatch: ...

    def supports(
        self, request: SearchRequest, config: dict[str, Any]
    ) -> tuple[bool, str | None]: ...


class BaseConnector:
    """Convenience lifecycle for connectors that discover then fetch records.

    API connectors can use cursor tokens, while website connectors can use
    approved page links. Override ``discover``/``fetch`` as needed. ``parse``
    and ``normalize`` are split so provider formats never leak into persistence.
    """

    def validate(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        return True, None

    def health_check(self, config: dict[str, Any]) -> bool:
        valid, _ = self.validate(config)
        return valid

    def supports(self, request: SearchRequest, config: dict[str, Any]) -> tuple[bool, str | None]:
        return True, None

    def discover(self, request: SearchRequest, config: dict[str, Any]):
        """Return source-specific identifiers or raw records; override per source."""
        raise NotImplementedError

    def fetch(self, item: Any, config: dict[str, Any]) -> Any:
        """Fetch one item where separate fetching is needed; local/API sources may pass through."""
        return item

    def parse(self, response: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert a fetched response/page to source-shaped record mappings."""
        if isinstance(response, dict):
            return [response]
        if isinstance(response, list):
            if not all(isinstance(row, dict) for row in response):
                raise ConnectorError(ConnectorErrorKind.INVALID_RESPONSE, "expected record objects")
            return response
        raise ConnectorError(ConnectorErrorKind.INVALID_RESPONSE, "unsupported response shape")

    def normalize(self, record: dict[str, Any], config: dict[str, Any]) -> Candidate:
        try:
            normalized = dict(record)
            candidate_fields = {
                "name",
                "country",
                "website",
                "city",
                "region",
                "address",
                "postal_code",
                "latitude",
                "longitude",
                "categories",
                "email",
                "phone",
                "description",
                "external_id",
                "source_url",
                "synthetic",
            }
            source_values = {key: value for key, value in record.items() if key in candidate_fields}
            normalized.setdefault("raw_values", source_values)
            return Candidate.model_validate(normalized)
        except ValidationError as exc:
            raise ConnectorError(
                ConnectorErrorKind.MALFORMED_RECORD,
                f"record validation failed: {exc.errors(include_input=False)}",
            ) from exc

    def pages(self, request: SearchRequest, config: dict[str, Any]):
        """Default single page; connectors may yield cursors/pages as appropriate."""
        yield self.discover(request, config)

    @staticmethod
    def rate_limit(config: dict[str, Any]) -> None:
        """Apply configured spacing between requests (never bypass source limits)."""
        delay = max(0.0, float(config.get("request_interval_seconds", 0)))
        if delay:
            time.sleep(delay)

    def collect(self, request: SearchRequest, config: dict[str, Any]) -> ConnectorBatch:
        batch = ConnectorBatch()
        index = 0
        try:
            for page in self.pages(request, config):
                records = page if isinstance(page, list) else [page]
                for item in records:
                    try:
                        self.rate_limit(config)
                        raw = self.fetch(item, config)
                        parsed = self.parse(raw, config)
                        for record in parsed:
                            try:
                                batch.records.append(self.normalize(record, config))
                            except ConnectorError as exc:
                                batch.issues.append(
                                    ConnectorIssue(exc.kind, str(exc), index, exc.retry_after)
                                )
                            index += 1
                    except ConnectorError as exc:
                        batch.issues.append(
                            ConnectorIssue(exc.kind, str(exc), index, exc.retry_after)
                        )
                        index += 1
        except ConnectorError:
            raise
        except TimeoutError as exc:
            raise ConnectorError(ConnectorErrorKind.TIMEOUT, "source request timed out") from exc
        return batch
