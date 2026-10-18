import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split())


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"https://{value}")
    try:
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    host = host.removeprefix("www.").rstrip(".")
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
    raw_values: dict[str, Any] | None = None

    @field_validator("country")
    @classmethod
    def country_upper(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("country must be ISO alpha-2")
        return value


class GeographicBounds(BaseModel):
    south: float = Field(ge=-90, le=90)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def bounded_area(self):
        height, width = self.north - self.south, self.east - self.west
        if height <= 0 or width <= 0:
            raise ValueError("bounds must have south < north and west < east")
        if height > 0.100000001 or width > 0.100000001:
            raise ValueError("collection bounds must fit within a 0.1 by 0.1 degree area")
        return self


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    source_ids: list[str] | None = Field(default=None, max_length=20)
    max_results: int = Field(default=100, ge=1, le=1000)
    bounds: GeographicBounds | None = None

    @field_validator("country")
    @classmethod
    def country_upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("query", "region", "city", "category")
    @classmethod
    def nonblank_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def unique_sources(self):
        if self.source_ids and len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must not contain duplicates")
        return self


class ScheduleRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    countries: list[str] = Field(min_length=1, max_length=20)
    region: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=200)
    source_ids: list[str] | None = Field(default=None, max_length=20)
    interval_minutes: int = Field(default=1440, ge=5, le=525600)
    max_results: int = Field(default=100, ge=1, le=1000)
    source_limits: dict[str, int] = Field(default_factory=dict, max_length=20)
    bounds: GeographicBounds | None = None
    enabled: bool = True
    allow_overlap: bool = False
    retry_attempts: int = Field(default=3, ge=0, le=5)

    @field_validator("query", "region", "city", "category")
    @classmethod
    def schedule_nonblank(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("must not be blank")
        return value

    @field_validator("countries")
    @classmethod
    def normalize_countries(cls, value: list[str]) -> list[str]:
        normalized = [country.upper() for country in value]
        if any(not re.fullmatch(r"[A-Z]{2}", country) for country in normalized):
            raise ValueError("countries must contain ISO alpha-2 codes")
        if len(set(normalized)) != len(normalized):
            raise ValueError("countries must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def schedule_limits(self):
        if self.source_ids and len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must not contain duplicates")
        if any(limit < 1 or limit > self.max_results for limit in self.source_limits.values()):
            raise ValueError("source limits must be between 1 and max_results")
        if self.source_ids and set(self.source_limits) - set(self.source_ids):
            raise ValueError("source_limits keys must be selected source IDs")
        return self


class SourceUpdate(BaseModel):
    enabled: bool
