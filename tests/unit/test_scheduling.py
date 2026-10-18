from datetime import UTC, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Job, Schedule, ScheduleExecution, Source, now
from app.services.scheduling import (
    dispatch_due_schedules,
    refresh_execution_status,
    transient_failure_kinds,
)


def make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_dispatch_claims_due_schedule_and_does_not_duplicate(monkeypatch):
    db = make_session()
    source = Source(
        code="dataset",
        name="Local dataset",
        source_type="test",
        connector="dataset",
        config={"path": "app/fixtures/synthetic_businesses.json"},
        enabled=True,
        review_status="approved",
        countries=["US", "GB"],
    )
    schedule = Schedule(
        query="bookshop",
        country="US",
        countries=["US", "GB"],
        source_ids=[],
        interval_minutes=30,
        max_results=40,
        source_limits={},
        retry_attempts=2,
        enabled=True,
        next_run_at=now() - timedelta(minutes=1),
    )
    db.add_all([source, schedule])
    db.flush()
    schedule.source_ids = [source.id]
    db.commit()
    queued = []

    job_ids = dispatch_due_schedules(db, queued.append, current=now())
    assert job_ids == queued
    assert len(job_ids) == 2
    jobs = db.query(Job).filter_by(schedule_id=schedule.id).order_by(Job.country).all()
    assert {job.country for job in jobs} == {"US", "GB"}
    assert all(job.max_results == 40 and job.retry_limit == 2 for job in jobs)
    run = db.query(ScheduleExecution).filter_by(schedule_id=schedule.id).one()
    assert run.status == "queued"
    assert set(run.job_ids) == set(job_ids)
    assert dispatch_due_schedules(db, queued.append, current=now()) == []
    assert db.query(Job).filter_by(schedule_id=schedule.id).count() == 2
    db.close()


def test_active_scheduled_job_creates_skipped_overlap_history():
    db = make_session()
    source = Source(
        code="dataset",
        name="Local dataset",
        source_type="test",
        connector="dataset",
        config={"path": "app/fixtures/synthetic_businesses.json"},
        enabled=True,
        review_status="approved",
        countries=["US"],
    )
    schedule = Schedule(
        query="bookshop",
        country="US",
        countries=["US"],
        source_ids=[],
        interval_minutes=10,
        enabled=True,
        allow_overlap=False,
        next_run_at=now() - timedelta(minutes=1),
    )
    db.add_all([source, schedule])
    db.flush()
    db.add(Job(query="bookshop", country="US", schedule_id=schedule.id, status="running"))
    db.commit()
    queued = []

    assert dispatch_due_schedules(db, queued.append, current=now()) == []
    assert queued == []
    run = db.query(ScheduleExecution).filter_by(schedule_id=schedule.id).one()
    assert run.status == "skipped_overlap"
    assert "still active" in run.error
    db.close()


def test_allow_overlap_creates_new_run_and_execution_status_tracks_child_jobs():
    db = make_session()
    source = Source(
        code="dataset",
        name="Local dataset",
        source_type="test",
        connector="dataset",
        config={"path": "app/fixtures/synthetic_businesses.json"},
        enabled=True,
        review_status="approved",
        countries=["US"],
    )
    schedule = Schedule(
        query="bookshop",
        country="US",
        countries=["US"],
        interval_minutes=10,
        enabled=True,
        allow_overlap=True,
        next_run_at=now() - timedelta(minutes=1),
    )
    db.add_all([source, schedule])
    db.flush()
    first_job = Job(query="bookshop", country="US", schedule_id=schedule.id, status="running")
    db.add(first_job)
    db.commit()
    queued = []
    assert dispatch_due_schedules(db, queued.append, current=now()) == queued
    assert len(queued) == 1
    second_job = db.get(Job, queued[0])
    execution = db.get(ScheduleExecution, second_job.schedule_execution_id)
    sibling = Job(
        query="bookshop",
        country="GB",
        schedule_id=schedule.id,
        schedule_execution_id=execution.id,
        status="succeeded",
    )
    db.add(sibling)
    db.flush()
    first_job.status = "succeeded"
    refresh_execution_status(db, second_job)
    assert execution.status == "running"
    second_job.status = "failed"
    refresh_execution_status(db, second_job)
    assert execution.status == "partial_success"
    assert execution.completed_at is not None
    db.close()


def test_disabled_schedule_is_not_dispatched():
    db = make_session()
    schedule = Schedule(
        query="shop",
        country="US",
        interval_minutes=5,
        enabled=False,
        next_run_at=now() - timedelta(minutes=1),
    )
    db.add(schedule)
    db.commit()
    assert dispatch_due_schedules(db, lambda _: pytest.fail("disabled schedule was queued")) == []
    assert db.query(ScheduleExecution).count() == 0
    db.close()


def test_transient_failures_are_retryable_but_policy_errors_are_not():
    transient = Job(
        query="shop",
        country="US",
        outcome={"sources": {"osm": {"status": "failed", "kind": "rate_limited"}}},
    )
    permanent = Job(
        query="shop",
        country="US",
        outcome={"sources": {"dataset": {"status": "failed", "kind": "configuration"}}},
    )
    assert transient_failure_kinds(transient) == ["rate_limited"]
    assert transient_failure_kinds(permanent) == []


def test_worker_marks_transient_job_retrying_and_schedules_backoff(monkeypatch):
    from app import db as database
    from app.tasks import celery_app

    db = make_session()
    job = Job(query="shop", country="US", retry_limit=2)
    db.add(job)
    db.commit()
    job_id = job.id
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.close()

    def record_transient_failure(target_job_id):
        session = factory()
        target = session.get(Job, target_job_id)
        target.status = "failed"
        target.outcome = {
            "sources": {
                "osm": {
                    "status": "failed",
                    "kind": "timeout",
                    "errors": [{"kind": "timeout", "retry_after": 45}],
                }
            }
        }
        session.commit()
        session.close()

    class RetrySignal(Exception):
        pass

    retry_kwargs = {}

    def retry(**kwargs):
        retry_kwargs.update(kwargs)
        raise RetrySignal

    monkeypatch.setattr(celery_app, "ingest_job", record_transient_failure)
    monkeypatch.setattr(database.session, "SessionLocal", factory)
    monkeypatch.setattr(celery_app.run_job, "retry", retry)
    with pytest.raises(RetrySignal):
        celery_app.run_job.run(job_id)

    session = factory()
    retried = session.get(Job, job_id)
    assert retried.status == "retrying"
    assert retried.retry_count == 1
    assert retry_kwargs["countdown"] == 45
    assert retry_kwargs["max_retries"] == 2
    enqueue_after = retried.last_enqueued_at
    if enqueue_after.tzinfo is None:
        enqueue_after = enqueue_after.replace(tzinfo=UTC)
    assert enqueue_after >= now() + timedelta(seconds=44)
    session.close()
