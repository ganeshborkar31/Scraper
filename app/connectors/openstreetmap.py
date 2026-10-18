"""Small, read-only OpenStreetMap business queries through Overpass API."""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.connectors.base import BaseConnector, ConnectorError, ConnectorErrorKind
from app.schemas import SearchRequest

OVERPASS_DEFAULT = "https://overpass-api.de/api/interpreter"
MAX_RESULTS = 100
MAX_BOUNDS_DEGREES = 0.1
MIN_REQUEST_INTERVAL_SECONDS = 10.0
CATEGORY_TAGS: dict[str, tuple[tuple[str, str], ...]] = {
    "bakery": (("shop", "bakery"),),
    "cafe": (("amenity", "cafe"),),
    "clothes": (("shop", "clothes"),),
    "convenience": (("shop", "convenience"),),
    "hardware": (("shop", "hardware"),),
    "pharmacy": (("amenity", "pharmacy"), ("shop", "chemist")),
    "restaurant": (("amenity", "restaurant"),),
    "supermarket": (("shop", "supermarket"),),
}

_request_lock = threading.Lock()
_last_request_at = 0.0


class OpenStreetMapConnector(BaseConnector):
    """Overpass connector: one bounded query per collection job, no detail scraping."""

    def validate(self, config: dict[str, Any]) -> tuple[bool, str | None]:
        endpoint = str(config.get("endpoint", OVERPASS_DEFAULT))
        parsed = urlparse(endpoint)
        valid_endpoint = (
            parsed.scheme == "https"
            and parsed.hostname == "overpass-api.de"
            and parsed.path == "/api/interpreter"
            and not parsed.query
            and not parsed.fragment
        )
        user_agent = str(config.get("user_agent", "")).strip()
        if not valid_endpoint:
            return False, "Overpass endpoint must be the reviewed HTTPS overpass-api.de interpreter"
        if (
            not user_agent
            or "python-requests" in user_agent.lower()
            or "python-httpx" in user_agent.lower()
        ):
            return False, "configure an identifying application User-Agent"
        return True, None

    def health_check(self, config: dict[str, Any]) -> bool:
        # Avoid a health probe that creates extra demand on the shared public API.
        return self.validate(config)[0]

    def supports(self, request: SearchRequest, config: dict[str, Any]) -> tuple[bool, str | None]:
        if request.bounds is None:
            return False, "OpenStreetMap requires bounds no larger than 0.1 by 0.1 degrees"
        category = (request.category or "").strip().lower()
        if category not in CATEGORY_TAGS:
            return False, f"unsupported OpenStreetMap category: {category or '(missing)'}"
        return True, None

    @staticmethod
    def build_query(request: SearchRequest, config: dict[str, Any]) -> str:
        if request.bounds is None:
            raise ConnectorError(
                ConnectorErrorKind.CONFIGURATION, "a small geographic bounds is required"
            )
        category = (request.category or "").strip().lower()
        tags = CATEGORY_TAGS.get(category)
        if not tags:
            raise ConnectorError(
                ConnectorErrorKind.CONFIGURATION,
                f"unsupported OSM category; choose one of: {', '.join(sorted(CATEGORY_TAGS))}",
            )
        bounds = request.bounds
        bbox = f"{bounds.south},{bounds.west},{bounds.north},{bounds.east}"
        max_results = min(
            MAX_RESULTS,
            request.max_results,
            max(1, int(config.get("max_results", MAX_RESULTS))),
        )
        selections = "\n".join(f'  nwr["{key}"="{value}"]({bbox});' for key, value in tags)
        # The server-side output cap plus strict bounds and timeout keep this request small.
        return f"[out:json][timeout:25][maxsize:536870912];\n(\n{selections}\n);\nout center {max_results};"

    def discover(self, request: SearchRequest, config: dict[str, Any]) -> dict[str, Any]:
        global _last_request_at
        valid, reason = self.validate(config)
        if not valid:
            raise ConnectorError(ConnectorErrorKind.CONFIGURATION, reason or "invalid OSM config")
        query = self.build_query(request, config)
        # Serialize this connector's requests and keep a conservative gap between calls.
        with _request_lock:
            wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            try:
                response = httpx.post(
                    str(config.get("endpoint", OVERPASS_DEFAULT)),
                    data={"data": query},
                    headers={"User-Agent": str(config["user_agent"]), "Accept": "application/json"},
                    timeout=httpx.Timeout(35.0, connect=10.0),
                )
            except httpx.TimeoutException as exc:
                raise ConnectorError(
                    ConnectorErrorKind.TIMEOUT, "Overpass request timed out"
                ) from exc
            except httpx.HTTPError as exc:
                raise ConnectorError(
                    ConnectorErrorKind.UNAVAILABLE, "Overpass service is unavailable"
                ) from exc
            finally:
                _last_request_at = time.monotonic()
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_seconds = None
            raise ConnectorError(
                ConnectorErrorKind.RATE_LIMITED,
                "Overpass rate limit reached",
                retry_after=retry_seconds,
            )
        if response.status_code in {401, 403}:
            raise ConnectorError(ConnectorErrorKind.AUTHENTICATION, "Overpass rejected the request")
        if response.status_code >= 500:
            raise ConnectorError(
                ConnectorErrorKind.TEMPORARY_SERVER, "Overpass returned a server error"
            )
        if response.status_code >= 400:
            raise ConnectorError(ConnectorErrorKind.INVALID_RESPONSE, "Overpass rejected the query")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ConnectorError(
                ConnectorErrorKind.INVALID_RESPONSE, "Overpass returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
            raise ConnectorError(
                ConnectorErrorKind.INVALID_RESPONSE, "Overpass response has no elements list"
            )
        return payload

    def parse(self, response: Any, config: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(response, dict) or not isinstance(response.get("elements"), list):
            raise ConnectorError(
                ConnectorErrorKind.INVALID_RESPONSE, "Overpass response has no elements list"
            )
        request = config.get("_request")
        if not isinstance(request, SearchRequest):
            raise ConnectorError(
                ConnectorErrorKind.CONFIGURATION, "collection request context is missing"
            )
        output = []
        for element in response["elements"]:
            if not isinstance(element, dict):
                output.append(element)
                continue
            candidate = self._candidate_fields(element, request)
            tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
            if tags.get("name") and request.query.casefold() not in str(tags["name"]).casefold():
                continue
            if (
                request.city
                and str(candidate.get("city") or "").casefold() != request.city.casefold()
            ):
                continue
            if (
                request.region
                and str(candidate.get("region") or "").casefold() != request.region.casefold()
            ):
                continue
            output.append(candidate)
        return output

    @staticmethod
    def _candidate_fields(element: dict[str, Any], request: SearchRequest) -> dict[str, Any]:
        tags = element.get("tags")
        if not isinstance(tags, dict):
            tags = {}
        location = element.get("center") if isinstance(element.get("center"), dict) else element
        osm_type, osm_id = element.get("type"), element.get("id")
        address_parts = [tags.get("addr:housenumber"), tags.get("addr:street")]
        address = str(
            tags.get("addr:full") or " ".join(str(part).strip() for part in address_parts if part)
        )
        category = next(
            (
                f"{key}:{tags[key]}"
                for key in ("shop", "amenity", "craft", "office")
                if tags.get(key)
            ),
            request.category or "business",
        )
        retained_tag_keys = {
            "name",
            "shop",
            "amenity",
            "craft",
            "office",
            "addr:full",
            "addr:housenumber",
            "addr:street",
            "addr:city",
            "addr:state",
            "addr:postcode",
            "website",
            "contact:website",
            "phone",
            "contact:phone",
        }
        return {
            "name": tags.get("name", ""),
            "country": request.country,
            "city": tags.get("addr:city"),
            "region": tags.get("addr:state"),
            "address": address or None,
            "postal_code": tags.get("addr:postcode"),
            "latitude": location.get("lat"),
            "longitude": location.get("lon"),
            "website": tags.get("website") or tags.get("contact:website"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "categories": [category],
            "external_id": f"{osm_type}/{osm_id}" if osm_type and osm_id is not None else None,
            "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
            if osm_type and osm_id is not None
            else None,
            "raw_values": {
                "type": osm_type,
                "id": osm_id,
                "tags": {key: value for key, value in tags.items() if key in retained_tag_keys},
                "lat": location.get("lat"),
                "lon": location.get("lon"),
            },
        }

    def collect(self, request: SearchRequest, config: dict[str, Any]):
        # Carry request fields through normalization without putting them in Source.config.
        enriched = {**config, "_request": request}
        return super().collect(request, enriched)
