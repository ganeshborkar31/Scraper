from typing import Protocol

from app.schemas import Candidate, SearchRequest


class Connector(Protocol):
    def validate(self, config: dict) -> tuple[bool, str | None]: ...
    def search(self, request: SearchRequest, config: dict) -> list[Candidate]: ...
