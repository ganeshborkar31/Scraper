from celery import Celery

from app.core.settings import settings
from app.services.ingest import ingest_job

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


@celery.task(
    name="run_job", autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3}
)
def run_job(job_id: str) -> None:
    ingest_job(job_id)


@celery.task(name="dispatch_schedules")
def dispatch_schedules() -> None:
    from datetime import timedelta

    from app.db.models import Job, Schedule, now
    from app.db.session import SessionLocal

    db = SessionLocal()
    current = now()
    queued_job_ids: list[str] = []
    for schedule in (
        db.query(Schedule).filter(Schedule.enabled.is_(True), Schedule.next_run_at <= current).all()
    ):
        active = (
            db.query(Job)
            .filter(
                Job.status.in_(["queued", "running"]),
                Job.query == schedule.query,
                Job.country == schedule.country,
            )
            .first()
        )
        if active:
            schedule.next_run_at = current + timedelta(minutes=schedule.interval_minutes)
            continue
        job = Job(
            query=schedule.query,
            country=schedule.country,
            source_ids=schedule.source_ids,
            max_results=100,
        )
        db.add(job)
        schedule.last_run_at = current
        schedule.next_run_at = current + timedelta(minutes=schedule.interval_minutes)
        db.flush()
        queued_job_ids.append(job.id)
    db.commit()
    db.close()
    for job_id in queued_job_ids:
        run_job.delay(job_id)


celery.conf.beat_schedule = {
    "check-persisted-schedules": {"task": "dispatch_schedules", "schedule": 60.0}
}
