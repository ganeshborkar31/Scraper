"""Validated transitions for collection job lifecycle states."""

from app.db.models import Job, now

JOB_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"retrying", "succeeded", "partial_success", "failed", "cancel_requested"},
    "retrying": {"running", "cancelled", "failed"},
    "cancel_requested": {"cancelled", "failed"},
    "succeeded": set(),
    "partial_success": {"retrying"},
    "failed": {"queued", "retrying"},
    "cancelled": set(),
}


class InvalidJobTransition(ValueError):
    pass


def transition_job(job: Job, target: str, *, error: str | None = None) -> None:
    current = job.status
    if target == current:
        return
    if target not in JOB_TRANSITIONS.get(current, set()):
        raise InvalidJobTransition(f"Invalid job transition: {current} -> {target}")
    job.status = target
    if error is not None:
        job.error = error
    if target in {"succeeded", "partial_success", "failed", "cancelled"}:
        job.completed_at = now()


def acknowledge_cancellation(job: Job) -> None:
    transition_job(job, "cancelled", error="Cancellation acknowledged by worker")
