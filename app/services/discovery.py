"""Source capability checks for bounded, on-demand searches."""

from app.connectors.registry import CONNECTORS
from app.db.models import Source
from app.schemas import SearchRequest


def source_compatibility(
    source: Source, request: SearchRequest, *, connector=None
) -> tuple[bool, str | None]:
    if not source.enabled or source.review_status != "approved":
        return False, "source is not enabled and approved"
    if "business" not in (source.record_types or ["business"]):
        return False, "source is not configured for business records"
    countries = {str(country).upper() for country in (source.countries or [])}
    if countries and request.country not in countries:
        return False, f"source does not support country {request.country}"
    connector = connector or CONNECTORS.get(source.connector)
    if connector is None:
        return False, f"connector '{source.connector}' is unavailable"
    supports = getattr(connector, "supports", None)
    compatible, reason = supports(request, source.config) if supports else (True, None)
    if not compatible:
        return False, reason
    valid, reason = connector.validate(source.config)
    if not valid:
        return False, reason or "source configuration is invalid"
    return True, None
