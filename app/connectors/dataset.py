import csv
import json
from pathlib import Path

from app.connectors.base import BaseConnector, ConnectorError, ConnectorErrorKind
from app.schemas import SearchRequest


class DatasetConnector(BaseConnector):
    """Imports administrator-controlled local CSV/JSON files; never fetches user URLs."""

    def validate(self, config: dict) -> tuple[bool, str | None]:
        path = Path(str(config.get("path", ""))).resolve()
        valid = path.is_file() and path.suffix.lower() in {".csv", ".json"}
        return valid, None if valid else "dataset must be an available CSV or JSON file"

    def discover(self, request: SearchRequest, config: dict) -> list[dict]:
        path = Path(config["path"]).resolve()
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                rows = (
                    json.load(handle)
                    if path.suffix.lower() == ".json"
                    else list(csv.DictReader(handle))
                )
        except json.JSONDecodeError as exc:
            raise ConnectorError(ConnectorErrorKind.INVALID_RESPONSE, "dataset contains invalid JSON") from exc
        except OSError as exc:
            raise ConnectorError(ConnectorErrorKind.UNAVAILABLE, "dataset file could not be read") from exc
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ConnectorError(ConnectorErrorKind.INVALID_RESPONSE, "dataset must contain a list of records")
        output = []
        terms = request.query.lower().split()
        for row in rows:
            row_country = row.get("country")
            if row_country and str(row_country).upper() != request.country:
                continue
            if request.city and str(row.get("city", "")).casefold() != request.city.casefold():
                continue
            name = row.get("name")
            if terms and name and not all(term in str(name).lower() for term in terms):
                continue
            output.append(row)
            if len(output) >= request.max_results:
                break
        return output
