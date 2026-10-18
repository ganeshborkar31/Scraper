from celery import Celery

from app.core.settings import settings
from app.db.models import now
from app.services.ingest import ingest_job
from app.services.job_state import transition_job
from app.services.scheduling import (
    refresh_execution_status,
    retry_after_seconds,
    transient_failure_kinds,
)

celery = Celery(
    "business_discovery",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.celery_app"],
)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@celery.task(bind=True, name="run_job", max_retries=5)
def run_job(self, job_id: str) -> None:
    ingest_job(job_id)
    from app.db.models import Job
    from app.db.session import SessionLocal

    db = SessionLocal()
    job = db.get(Job, job_id)
    if not job:
        db.close()
        return
    transient = transient_failure_kinds(job)
    retry_limit = min(5, max(0, job.retry_limit))
    if (
        job.status not in {"failed", "partial_success"}
        or not transient
        or job.retry_count >= retry_limit
    ):
        db.close()
        return
    job.retry_count += 1
    backoff = min(600, 30 * (2 ** (job.retry_count - 1)))
    countdown = max(backoff, retry_after_seconds(job) or 0)
    # Recovery must not bypass a source's Retry-After while Celery holds the retry.
    from datetime import timedelta

    job.last_enqueued_at = now() + timedelta(seconds=countdown)
    transition_job(
        job,
        "retrying",
        error=f"Retry {job.retry_count}/{retry_limit} after transient source failure: {', '.join(transient)}",
    )
    refresh_execution_status(db, job)
    db.commit()
    db.close()
    raise self.retry(
        exc=RuntimeError("transient source failure"),
        countdown=countdown,
        max_retries=retry_limit,
    )


@celery.task(name="dispatch_schedules")
def dispatch_schedules() -> None:
    from app.db.session import SessionLocal
    from app.services.scheduling import dispatch_due_schedules

    db = SessionLocal()
    try:
        dispatch_due_schedules(db, run_job.delay)
    finally:
        db.close()


@celery.task(name="recover_stale_jobs")
def recover_stale_jobs() -> None:
    from app.db.session import SessionLocal
    from app.services.scheduling import recover_jobs

    db = SessionLocal()
    try:
        recover_jobs(db, run_job.delay)
    finally:
        db.close()


celery.conf.beat_schedule = {
    "check-persisted-schedules": {"task": "dispatch_schedules", "schedule": 60.0},
    "recover-abandoned-jobs": {"task": "recover_stale_jobs", "schedule": 60.0},
}
