"""In-memory fixture connector for deterministic, offline pipeline tests."""

from typing import Any

from app.connectors.base import BaseConnector
from app.schemas import SearchRequest


class FixtureConnector(BaseConnector):
    """Test-only connector; records are supplied in config and never use a network."""

    def validate(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        rows = config.get("records")
        return (isinstance(rows, list), None if isinstance(rows, list) else "records must be a list")

    def discover(self, request: SearchRequest, config: dict[str, Any]):
        rows = config.get("records", [])
        page_size = max(1, int(config.get("page_size", len(rows) or 1)))
        for offset in range(0, len(rows), page_size):
            yield rows[offset : offset + page_size]

    def pages(self, request: SearchRequest, config: dict[str, Any]):
        yield from self.discover(request, config)

