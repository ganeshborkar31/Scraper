"""Schedule claiming, dispatch, and run-history status synchronization."""

from datetime import timedelta

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.db.models import Job, Schedule, ScheduleExecution, Source, now
from app.schemas import SearchRequest
from app.services.discovery import source_compatibility

TRANSIENT_KINDS = {"timeout", "rate_limited", "temporary_server", "unavailable"}
TERMINAL_JOB_STATUSES = {"succeeded", "partial_success", "failed", "cancelled"}


def transient_failure_kinds(job: Job) -> list[str]:
    kinds = set()
    for result in (job.outcome or {}).get("sources", {}).values():
        if result.get("retry_suppressed"):
            continue
        if result.get("kind") in TRANSIENT_KINDS:
            kinds.add(result["kind"])
        for issue in result.get("errors", []):
            if issue.get("kind") in TRANSIENT_KINDS:
                kinds.add(issue["kind"])
    return sorted(kinds)


def retry_after_seconds(job: Job) -> float | None:
    delays = []
    for result in (job.outcome or {}).get("sources", {}).values():
        delay = result.get("retry_after")
        if isinstance(delay, int | float) and delay >= 0:
            delays.append(float(delay))
        for issue in result.get("errors", []):
            delay = issue.get("retry_after")
            if isinstance(delay, int | float) and delay >= 0:
                delays.append(float(delay))
    return max(delays) if delays else None


def refresh_execution_status(db: Session, job: Job) -> None:
    if not job.schedule_execution_id:
        return
    execution = db.get(ScheduleExecution, job.schedule_execution_id)
    if not execution:
        return
    jobs = db.query(Job).filter_by(schedule_execution_id=execution.id).all()
    statuses = [child.status for child in jobs]
    if any(status in {"queued", "running", "retrying", "cancel_requested"} for status in statuses):
        execution.completed_at = None
        execution.status = "retrying" if "retrying" in statuses else "running"
        return
    if statuses and all(status in TERMINAL_JOB_STATUSES for status in statuses):
        execution.completed_at = now()
        execution.status = (
            "succeeded"
            if all(status == "succeeded" for status in statuses)
            else "failed"
            if all(status == "failed" for status in statuses)
            else "cancelled"
            if all(status == "cancelled" for status in statuses)
            else "partial_success"
        )


def dispatch_due_schedules(db: Session, enqueue, current=None) -> list[str]:
    """Atomically claim due schedules and enqueue each eligible country job once."""
    current = current or now()
    due = (
        db.query(Schedule)
        .filter(
            Schedule.enabled.is_(True),
            Schedule.next_run_at.is_not(None),
            Schedule.next_run_at <= current,
        )
        .order_by(Schedule.next_run_at, Schedule.id)
        .all()
    )
    queued_ids: list[str] = []
    for schedule in due:
        scheduled_for = schedule.next_run_at
        next_time = current + timedelta(minutes=schedule.interval_minutes)
        claim = db.execute(
            update(Schedule)
            .where(
                Schedule.id == schedule.id,
                Schedule.enabled.is_(True),
                Schedule.next_run_at == scheduled_for,
            )
            .values(next_run_at=next_time, last_run_at=current, last_error=None)
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            db.rollback()
            continue
        active = (
            db.query(Job)
            .filter(
                Job.schedule_id == schedule.id,
                Job.status.in_(["queued", "running", "retrying", "cancel_requested"]),
            )
            .first()
        )
        execution = ScheduleExecution(
            schedule_id=schedule.id,
            scheduled_for=scheduled_for,
            started_at=current,
            status="queued",
            job_ids=[],
        )
        db.add(execution)
        db.flush()
        if active and not schedule.allow_overlap:
            execution.status = "skipped_overlap"
            execution.completed_at = current
            execution.error = f"Skipped because scheduled job {active.id} is still active"
            db.commit()
            continue

        selected_sources = (
            db.query(Source).filter(Source.id.in_(schedule.source_ids)).all()
            if schedule.source_ids
            else db.query(Source).all()
        )
        eligible_countries = schedule.countries or [schedule.country]
        created_jobs = []
        try:
            for country in eligible_countries:
                request = SearchRequest(
                    query=schedule.query,
                    country=country,
                    region=schedule.region,
                    city=schedule.city,
                    category=schedule.category,
                    source_ids=schedule.source_ids or None,
                    max_results=schedule.max_results,
                    bounds=schedule.bounds,
                )
                compatible, unsupported = [], []
                for source in selected_sources:
                    supported, reason = source_compatibility(source, request)
                    if supported:
                        compatible.append(source)
                    else:
                        unsupported.append(f"{source.code}: {reason}")
                if not compatible:
                    raise ValueError(
                        f"No eligible source supports country {country}. " + "; ".join(unsupported)
                    )
                if schedule.source_ids and unsupported:
                    raise ValueError(
                        f"Selected source cannot support country {country}. "
                        + "; ".join(unsupported)
                    )
                if len(compatible) > 20:
                    raise ValueError(f"More than 20 sources support country {country}")
                job = Job(
                    query=schedule.query,
                    country=country,
                    region=schedule.region,
                    city=schedule.city,
                    category=schedule.category,
                    source_ids=[source.id for source in compatible],
                    bounds=schedule.bounds,
                    max_results=schedule.max_results,
                    source_limits=schedule.source_limits or {},
                    retry_limit=schedule.retry_attempts,
                    job_type="collection",
                    trigger_type="scheduled",
                    schedule_id=schedule.id,
                    schedule_execution_id=execution.id,
                )
                db.add(job)
                db.flush()
                created_jobs.append(job)
        except Exception as exc:
            execution.status = "failed"
            execution.completed_at = current
            execution.error = str(exc)[:1000]
            schedule.last_error = execution.error
            db.commit()
            continue

        execution.job_ids = [job.id for job in created_jobs]
        db.commit()
        queued_for_execution = 0
        queue_errors = []
        for job in created_jobs:
            try:
                enqueue(job.id)
                queued_ids.append(job.id)
                queued_for_execution += 1
            except Exception as exc:
                db.refresh(job)
                # Keep the durable work item queued so periodic recovery retries it.
                job.error = f"Background queue unavailable: {str(exc)[:250]}"
                job.last_enqueued_at = now() - timedelta(seconds=300)
                execution.error = str(exc)[:1000]
                schedule.last_error = "Background queue unavailable"
                queue_errors.append(str(exc))
        if queue_errors:
            if queued_for_execution:
                refresh_execution_status(db, created_jobs[0])
            else:
                execution.status = "queued"
                execution.completed_at = None
            db.commit()
    return queued_ids


def recover_jobs(
    db: Session, enqueue, current=None, *, queued_timeout_seconds: int = 300
) -> list[str]:
    """Requeue orphaned messages and recover workers abandoned past job deadlines."""
    current = current or now()
    requeued: list[str] = []
    queued_cutoff = current - timedelta(seconds=queued_timeout_seconds)
    orphaned = (
        db.query(Job)
        .filter(
            Job.status.in_(["queued", "retrying"]),
            or_(
                Job.last_enqueued_at <= queued_cutoff,
                (Job.last_enqueued_at.is_(None)) & (Job.created_at <= queued_cutoff),
            ),
        )
        .order_by(Job.created_at)
        .all()
    )
    for job in orphaned:
        previous_enqueued_at = job.last_enqueued_at
        claim = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.status == job.status,
                Job.last_enqueued_at == previous_enqueued_at,
            )
            .values(last_enqueued_at=current)
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            db.rollback()
            continue
        db.commit()
        try:
            enqueue(job.id)
            requeued.append(job.id)
        except Exception as exc:
            db.refresh(job)
            job.error = f"Recovery enqueue failed: {str(exc)[:300]}"
            db.commit()

    stale_running = (
        db.query(Job)
        .filter(
            Job.status == "running",
            or_(Job.last_attempt_at.is_not(None), Job.started_at.is_not(None)),
        )
        .order_by(Job.last_attempt_at)
        .all()
    )
    for job in stale_running:
        attempt = job.last_attempt_at or job.started_at
        if attempt.tzinfo is None:
            attempt = attempt.replace(tzinfo=current.tzinfo)
        if (current - attempt).total_seconds() <= job.execution_timeout_seconds:
            continue
        if job.retry_count >= min(job.retry_limit, 5):
            target = "failed"
            values = {
                "status": target,
                "completed_at": current,
                "worker_lease_token": None,
                "error": "Worker lease expired and retry limit was exhausted",
            }
        else:
            target = "retrying"
            values = {
                "status": target,
                "retry_count": job.retry_count + 1,
                "last_enqueued_at": current,
                "last_attempt_at": None,
                "worker_lease_token": None,
                "error": "Worker lease expired; job queued for recovery",
            }
        claim = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.status == "running",
                Job.last_attempt_at == job.last_attempt_at,
                Job.started_at == job.started_at,
                Job.worker_lease_token == job.worker_lease_token,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            db.rollback()
            continue
        db.commit()
        db.refresh(job)
        if target == "failed":
            job.error = values["error"]
        refresh_execution_status(db, job)
        db.commit()
        if target == "retrying":
            try:
                enqueue(job.id)
                requeued.append(job.id)
            except Exception as exc:
                db.refresh(job)
                job.error = f"Recovery enqueue failed: {str(exc)[:300]}"
                db.commit()
    stale_cancellations = (
        db.query(Job)
        .filter(
            Job.status == "cancel_requested",
            or_(Job.last_attempt_at.is_not(None), Job.started_at.is_not(None)),
        )
        .all()
    )
    for job in stale_cancellations:
        attempt = job.last_attempt_at or job.started_at
        if attempt.tzinfo is None:
            attempt = attempt.replace(tzinfo=current.tzinfo)
        if (current - attempt).total_seconds() <= job.execution_timeout_seconds:
            continue
        claim = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "cancel_requested")
            .values(
                status="cancelled",
                completed_at=current,
                worker_lease_token=None,
                error="Cancellation reconciled after worker lease expired",
            )
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount == 1:
            db.commit()
            db.refresh(job)
            refresh_execution_status(db, job)
            db.commit()
    return requeued
