import csv
import json
from pathlib import Path

from app.schemas import Candidate, SearchRequest


class DatasetConnector:
    """Imports administrator-controlled local CSV/JSON files; never fetches user URLs."""

    def validate(self, config: dict) -> tuple[bool, str | None]:
        path = Path(str(config.get("path", ""))).resolve()
        return (
            path.is_file() and path.suffix.lower() in {".csv", ".json"},
            None if path.is_file() else "dataset file unavailable",
        )

    def search(self, request: SearchRequest, config: dict) -> list[Candidate]:
        path = Path(config["path"])
        with path.open(encoding="utf-8", newline="") as handle:
            rows = (
                json.load(handle)
                if path.suffix.lower() == ".json"
                else list(csv.DictReader(handle))
            )
        output = []
        terms = request.query.lower().split()
        for row in rows:
            try:
                candidate = Candidate.model_validate(row)
            except Exception:
                continue
            if candidate.country != request.country:
                continue
            if request.city and (candidate.city or "").casefold() != request.city.casefold():
                continue
            if terms and not all(term in candidate.name.lower() for term in terms):
                continue
            output.append(candidate)
            if len(output) >= request.max_results:
                break
        return output
