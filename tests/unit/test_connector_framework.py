from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.connectors.base import ConnectorError, ConnectorErrorKind
from app.connectors.fixture import FixtureConnector
from app.db.models import Base, Job, Source, SourceRecord
from app.schemas import SearchRequest
from app.services import ingest


def test_fixture_connector_paginates_and_reports_malformed_records():
    connector = FixtureConnector()
    request = SearchRequest(query="sample", country="US")
    batch = connector.collect(
        request,
        {
            "page_size": 1,
            "records": [
                {"name": "Sample Shop", "country": "US", "external_id": "ok"},
                {"name": "", "country": "US", "external_id": "bad"},
            ],
        },
    )
    assert len(batch.records) == 1
    assert batch.records[0].external_id == "ok"
    assert len(batch.issues) == 1
    assert batch.issues[0].kind == ConnectorErrorKind.MALFORMED_RECORD


def test_ingest_isolates_connector_errors_and_keeps_provenance(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    healthy = Source(
        code="fixture-ok", name="Fixture", source_type="test", connector="fixture",
        enabled=True, review_status="approved", config={"records": [
            {"name": "Sample Shop", "country": "US", "external_id": "sample-1",
             "source_url": "https://fixture.invalid/sample-1"}
        ]},
    )
    failing = Source(
        code="fixture-fail", name="Failing fixture", source_type="test", connector="fail",
        enabled=True, review_status="approved", config={},
    )
    job = Job(query="sample", country="US", max_results=10)
    db.add_all([healthy, failing, job])
    db.commit()
    job_id = job.id
    db.close()

    class FailingConnector:
        def validate(self, config):
            return True, None

        def health_check(self, config):
            return True

        def collect(self, request, config):
            raise ConnectorError(ConnectorErrorKind.RATE_LIMITED, "retry later", retry_after=30)

    monkeypatch.setattr(ingest, "SessionLocal", factory)
    monkeypatch.setattr(
        ingest, "CONNECTORS", {"fixture": FixtureConnector(), "fail": FailingConnector()}
    )

    ingest.ingest_job(job_id)

    db = factory()
    stored_job = db.get(Job, job_id)
    assert stored_job.status == "partial_success"
    assert stored_job.outcome["sources"]["fixture-fail"]["kind"] == "rate_limited"
    record = db.query(SourceRecord).one()
    assert record.source_identifier == "fixture-ok"
    assert record.source_name == "Fixture"
    assert record.source_url == "https://fixture.invalid/sample-1"
    db.close()
