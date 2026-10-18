from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Job, Source, SourceRecord, now
from app.schemas import Candidate
from app.services.ingest import _job_control, ingest_job, upsert
from app.services.job_state import InvalidJobTransition, transition_job
from app.services.scheduling import recover_jobs


def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_job_record_is_idempotent_within_one_job_but_a_new_job_keeps_provenance():
    _, factory = database()
    db = factory()
    source = Source(code="fixture", name="Fixture", source_type="test", connector="fixture")
    first_job = Job(query="shop", country="US")
    second_job = Job(query="shop", country="US")
    db.add_all([source, first_job, second_job])
    db.commit()
    candidate = Candidate(name="Test Shop", country="US", external_id="fixture-1")

    assert upsert(db, source, candidate, first_job.id) == "created"
    db.commit()
    assert upsert(db, source, candidate, first_job.id) == "idempotent"
    assert upsert(db, source, candidate, second_job.id) == "duplicate"
    db.commit()
    assert db.query(SourceRecord).count() == 2
    assert db.query(SourceRecord).filter_by(collection_job_id=first_job.id).count() == 1
    db.close()


def test_worker_claim_makes_duplicate_delivery_a_noop(monkeypatch):
    from app.services import ingest

    _, factory = database()
    db = factory()
    source = Source(
        code="fixture",
        name="Fixture",
        source_type="test",
        connector="fixture",
        enabled=True,
        review_status="approved",
        config={"records": [{"name": "Test Shop", "country": "US", "external_id": "1"}]},
    )
    job = Job(query="test", country="US", source_ids=[], max_results=10)
    db.add_all([source, job])
    db.commit()
    job_id = job.id
    db.close()
    monkeypatch.setattr(ingest, "SessionLocal", factory)
    from app.connectors.fixture import FixtureConnector

    monkeypatch.setattr(ingest, "CONNECTORS", {"fixture": FixtureConnector()})

    ingest_job(job_id)
    first_session = factory()
    first_outcome = first_session.get(Job, job_id).outcome
    first_session.close()
    ingest_job(job_id)
    check = factory()
    stored = check.get(Job, job_id)
    assert stored.status == "succeeded"
    assert stored.outcome == first_outcome
    assert check.query(SourceRecord).count() == 1
    check.close()


def test_cancel_queued_job_is_acknowledged_and_running_job_waits_for_worker(monkeypatch):
    from app import main
    from app.services import ingest

    _, factory = database()
    db = factory()
    queued = Job(query="queued", country="US")
    running = Job(query="running", country="US", status="running", last_attempt_at=now())
    db.add_all([queued, running])
    db.commit()
    queued_id, running_id = queued.id, running.id

    assert main.cancel_job(queued_id, _="admin", db=db)["status"] == "cancelled"
    assert main.cancel_job(running_id, _="admin", db=db)["status"] == "cancel_requested"
    monkeypatch.setattr(ingest, "refresh_execution_status", lambda *_: None)
    running = db.get(Job, running_id)
    assert _job_control(db, running) == "cancelled"
    assert running.status == "cancelled"
    db.close()


def test_worker_recovery_requeues_orphans_once_and_fails_exhausted_stale_jobs():
    _, factory = database()
    db = factory()
    past = now() - timedelta(hours=2)
    queued = Job(
        query="orphaned",
        country="US",
        status="queued",
        created_at=past,
        last_enqueued_at=past,
    )
    recoverable = Job(
        query="stale",
        country="US",
        status="running",
        last_attempt_at=past,
        execution_timeout_seconds=60,
        retry_limit=2,
    )
    exhausted = Job(
        query="exhausted",
        country="US",
        status="running",
        last_attempt_at=past,
        execution_timeout_seconds=60,
        retry_limit=1,
        retry_count=1,
    )
    db.add_all([queued, recoverable, exhausted])
    db.commit()
    queued_id, recoverable_id, exhausted_id = queued.id, recoverable.id, exhausted.id
    enqueued = []
    assert set(recover_jobs(db, enqueued.append, current=now(), queued_timeout_seconds=30)) == {
        queued_id,
        recoverable_id,
    }
    assert db.get(Job, queued_id).status == "queued"
    assert db.get(Job, recoverable_id).status == "retrying"
    assert db.get(Job, recoverable_id).retry_count == 1
    assert db.get(Job, exhausted_id).status == "failed"
    assert recover_jobs(db, enqueued.append, current=now(), queued_timeout_seconds=30) == []
    db.close()


def test_invalid_job_transition_is_rejected():
    job = Job(query="shop", country="US", status="queued")
    with pytest.raises(InvalidJobTransition, match="queued -> succeeded"):
        transition_job(job, "succeeded")
    transition_job(job, "running")
    transition_job(job, "cancel_requested")
    transition_job(job, "cancelled")
    assert job.status == "cancelled"


def test_stale_running_cancellation_is_reconciled_after_execution_deadline():
    _, factory = database()
    db = factory()
    stale_start = now() - timedelta(hours=2)
    job = Job(
        query="cancel me",
        country="US",
        status="cancel_requested",
        started_at=stale_start,
        last_attempt_at=stale_start,
        execution_timeout_seconds=60,
    )
    db.add(job)
    db.commit()

    assert recover_jobs(db, lambda _job_id: None, current=now()) == []
    assert db.get(Job, job.id).status == "cancelled"
    assert db.get(Job, job.id).completed_at is not None
    db.close()


def test_superseded_worker_lease_cannot_continue_after_recovery():
    _, factory = database()
    db = factory()
    job = Job(
        query="leased",
        country="US",
        status="running",
        worker_lease_token="new-attempt",
    )
    db.add(job)
    db.commit()

    assert _job_control(db, job, "old-attempt") == "superseded"
    assert job.status == "running"
    assert job.worker_lease_token == "new-attempt"
    db.close()


def test_permanent_authentication_failure_is_not_retried(monkeypatch):
    from app.db import session as db_session
    from app.tasks import celery_app

    _, factory = database()
    db = factory()
    job = Job(query="shop", country="US", status="queued", retry_limit=5)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    def record_authentication_failure(target_id):
        session = factory()
        target = session.get(Job, target_id)
        target.status = "failed"
        target.outcome = {"sources": {"source": {"status": "failed", "kind": "authentication"}}}
        session.commit()
        session.close()

    def should_not_retry(**_kwargs):
        pytest.fail("permanent authentication errors must not be retried")

    monkeypatch.setattr(celery_app, "ingest_job", record_authentication_failure)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(celery_app.run_job, "retry", should_not_retry)
    assert celery_app.run_job.run(job_id) is None
    session = factory()
    stored = session.get(Job, job_id)
    assert stored.status == "failed"
    assert stored.retry_count == 0
    session.close()
