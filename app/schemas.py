import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", value.lower()).split())


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower().removeprefix("www.").rstrip(".")
    return host if "." in host and " " not in host else None


class Candidate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    country: str = Field(min_length=2, max_length=2)
    website: str | None = None
    city: str | None = None
    region: str | None = None
    address: str | None = None
    postal_code: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    categories: list[str] = Field(default_factory=list)
    email: str | None = None
    phone: str | None = None
    description: str | None = None
    external_id: str | None = None
    source_url: str | None = None
    synthetic: bool = False

    @field_validator("country")
    @classmethod
    def country_upper(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("country must be ISO alpha-2")
        return value


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    country: str = Field(min_length=2, max_length=2)
    city: str | None = None
    category: str | None = None
    source_ids: list[str] | None = None
    max_results: int = Field(default=100, ge=1, le=1000)

    @field_validator("country")
    @classmethod
    def country_upper(cls, value: str) -> str:
        return value.upper()
