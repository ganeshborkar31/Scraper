import json
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors import openstreetmap
from app.connectors.base import ConnectorError, ConnectorErrorKind
from app.connectors.openstreetmap import OpenStreetMapConnector
from app.db.models import Base, Business, Job, Source, SourceRecord
from app.schemas import SearchRequest
from app.services import ingest

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "overpass_response.json"
CONFIG = {
    "endpoint": "https://overpass-api.de/api/interpreter",
    "user_agent": "BusinessDataDiscovery-test/0.1",
    "max_results": 4,
}


def request():
    return SearchRequest(
        query="bakery",
        country="US",
        category="bakery",
        max_results=5,
        bounds={"south": 37.1, "west": -122.2, "north": 37.2, "east": -122.1},
    )


def test_overpass_saved_response_parses_fields_and_reports_invalid_record(monkeypatch):
    payload = json.loads(FIXTURE_PATH.read_text())
    posted = {}

    class Response:
        def __init__(self):
            self.status_code = 200
            self.headers = {}

        @staticmethod
        def json():
            return payload

    def fake_post(url, *, data, headers, timeout):
        posted.update(url=url, data=data, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(openstreetmap.httpx, "post", fake_post)
    monkeypatch.setattr(openstreetmap, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(openstreetmap, "_last_request_at", time.monotonic())
    connector = OpenStreetMapConnector()
    batch = connector.collect(request(), CONFIG)

    assert 'nwr["shop"="bakery"]' in posted["data"]["data"]
    assert "out center 4;" in posted["data"]["data"]
    assert posted["headers"]["User-Agent"] == CONFIG["user_agent"]
    assert len(batch.records) == 2
    first = batch.records[0]
    assert first.name == "Fixture Bakery"
    assert first.categories == ["shop:bakery"]
    assert first.address == "10 Market Street"
    assert first.website == "https://bakery.example"
    assert first.phone == "+1-555-0100"
    assert (first.latitude, first.longitude) == (37.123, -122.123)
    assert first.external_id == "node/123456"
    assert first.source_url == "https://www.openstreetmap.org/node/123456"
    assert batch.issues[0].kind.value == "malformed_record"


def test_osm_records_persist_with_provenance_and_repeat_without_duplicate_businesses(monkeypatch):
    payload = json.loads(FIXTURE_PATH.read_text())

    class FixtureOSMConnector(OpenStreetMapConnector):
        def discover(self, search_request, config):
            return payload

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    source = Source(
        code="openstreetmap-overpass",
        name="OpenStreetMap via Overpass API",
        source_type="open_data",
        connector="openstreetmap",
        enabled=True,
        review_status="approved",
        attribution="© OpenStreetMap contributors, ODbL 1.0",
        config=CONFIG,
    )
    job = Job(
        query="bakery",
        country="US",
        category="bakery",
        bounds=request().bounds.model_dump(),
        max_results=5,
    )
    db.add_all([source, job])
    db.commit()
    job_id = job.id
    db.close()

    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(ingest, "CONNECTORS", {"openstreetmap": FixtureOSMConnector()})
    ingest.ingest_job(job_id)
    ingest.ingest_job(job_id)

    db = factory()
    stored_job = db.get(Job, job_id)
    assert stored_job.status == "partial_success"
    assert (
        stored_job.outcome["sources"]["openstreetmap-overpass"]["errors"][0]["kind"]
        == "malformed_record"
    )
    assert db.query(Business).count() == 2
    assert db.query(SourceRecord).count() == 2
    record = db.query(SourceRecord).filter_by(external_id="node/123456").first()
    assert record.source_identifier == "openstreetmap-overpass"
    assert record.source_url == "https://www.openstreetmap.org/node/123456"
    assert record.metadata_json["attribution"] == "© OpenStreetMap contributors, ODbL 1.0"
    db.close()


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, ConnectorErrorKind.RATE_LIMITED),
        (403, ConnectorErrorKind.AUTHENTICATION),
        (503, ConnectorErrorKind.TEMPORARY_SERVER),
        (400, ConnectorErrorKind.INVALID_RESPONSE),
    ],
)
def test_overpass_http_failures_are_classified(monkeypatch, status, kind):
    class Response:
        def __init__(self):
            self.status_code = status
            self.headers = {"Retry-After": "45"}

    monkeypatch.setattr(openstreetmap.httpx, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(openstreetmap, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(openstreetmap, "_last_request_at", time.monotonic())
    with pytest.raises(ConnectorError) as error:
        OpenStreetMapConnector().collect(request(), CONFIG)
    assert error.value.kind == kind
    if status == 429:
        assert error.value.retry_after == 45


def test_overpass_timeout_is_classified(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("fixture timeout")

    monkeypatch.setattr(openstreetmap.httpx, "post", timeout)
    monkeypatch.setattr(openstreetmap, "MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(openstreetmap, "_last_request_at", time.monotonic())
    with pytest.raises(ConnectorError) as error:
        OpenStreetMapConnector().collect(request(), CONFIG)
    assert error.value.kind == ConnectorErrorKind.TIMEOUT


def test_overpass_requires_small_bounds_and_known_category():
    connector = OpenStreetMapConnector()
    with pytest.raises(ConnectorError, match="unsupported OSM category"):
        connector.build_query(request().model_copy(update={"category": "unknown"}), CONFIG)
    with pytest.raises(ValueError, match="collection bounds"):
        SearchRequest(
            query="bakery",
            country="US",
            category="bakery",
            bounds={"south": 37, "west": -123, "north": 38, "east": -122},
        )
