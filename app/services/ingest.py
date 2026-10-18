import hashlib
import json
import logging
from datetime import UTC
from uuid import uuid4

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError

from app.connectors.base import ConnectorError, ConnectorErrorKind
from app.connectors.registry import CONNECTORS
from app.db.models import Business, MatchDecision, Source, SourceRecord, now
from app.db.session import SessionLocal
from app.schemas import Candidate, SearchRequest
from app.services.discovery import source_compatibility
from app.services.job_state import acknowledge_cancellation, transition_job
from app.services.matching import MatchClass, MatchingRules, best_match
from app.services.normalization import (
    normalize_address,
    normalize_coordinates,
    normalize_domain,
    normalize_name,
    normalize_phone,
)
from app.services.scheduling import refresh_execution_status

logger = logging.getLogger(__name__)


def ingest_job(job_id: str) -> None:
    db = SessionLocal()
    from app.db.models import Job

    claimed_at = now()
    lease_token = str(uuid4())
    claim = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(["queued", "retrying"]))
        .values(
            status="running",
            started_at=func.coalesce(Job.started_at, claimed_at),
            last_attempt_at=claimed_at,
            worker_lease_token=lease_token,
        )
    )
    if claim.rowcount != 1:
        db.rollback()
        db.close()
        return
    db.commit()
    job = db.get(Job, job_id)
    if not job:
        db.close()
        return
    refresh_execution_status(db, job)
    db.commit()
    logger.info(
        "collection_job_started",
        extra={"job_id": job.id, "job_type": job.job_type, "trigger_type": job.trigger_type},
    )
    per_source: dict[str, dict] = dict((job.outcome or {}).get("sources", {}))
    is_retry = job.retry_count > 0
    if is_retry:
        per_source.pop("__worker__", None)
    request = SearchRequest(
        query=job.query,
        country=job.country,
        region=job.region,
        city=job.city,
        category=job.category,
        source_ids=job.source_ids,
        max_results=job.max_results,
        bounds=job.bounds,
    )
    sources = (
        db.query(Source).filter(Source.enabled.is_(True), Source.review_status == "approved").all()
    )
    if job.source_ids:
        sources = [source for source in sources if source.id in job.source_ids]
    for source in sources:
        control = _job_control(db, job, lease_token)
        if control != "running":
            db.close()
            return
        previous = per_source.get(source.code, {})
        if is_retry and previous.get("status") == "succeeded":
            continue
        if is_retry and not _source_has_transient_error(previous):
            continue
        connector = CONNECTORS.get(source.connector)
        prior_accepted = sum(int(result.get("accepted", 0)) for result in per_source.values())
        remaining_records = max(0, job.max_results - prior_accepted)
        if remaining_records == 0:
            if previous:
                per_source[source.code] = {
                    **previous,
                    "status": "partial_success" if previous.get("errors") else "succeeded",
                    "retry_suppressed": "job record limit reached",
                }
            else:
                per_source[source.code] = {
                    "status": "skipped",
                    "reason": "job record limit reached",
                }
            continue
        try:
            if connector is None:
                raise ValueError("connector is unavailable")
            compatible, reason = source_compatibility(source, request, connector=connector)
            if not compatible:
                per_source[source.code] = {"status": "skipped", "reason": reason}
                continue
            valid, reason = connector.validate(source.config)
            if not valid:
                raise ValueError(reason or "connector is not configured")
            source_limit = (job.source_limits or {}).get(
                source.id, (job.source_limits or {}).get(source.code, request.max_results)
            )
            source_request = request.model_copy(
                update={
                    "max_results": max(
                        1, min(remaining_records, request.max_results, int(source_limit))
                    )
                }
            )
            batch = connector.collect(source_request, source.config)
            control = _job_control(db, job, lease_token)
            if control != "running":
                db.close()
                return
            records = batch.records[:remaining_records]
            source_created = source_updated = source_duplicates = 0
            source_accepted = 0
            source_discovered = 0
            for item in records:
                try:
                    with db.begin_nested():
                        result = upsert(db, source, item, job_id=job.id)
                except IntegrityError:
                    key, _ = _candidate_job_key(item)
                    existing = (
                        db.query(SourceRecord)
                        .filter_by(
                            collection_job_id=job.id,
                            source_id=source.id,
                            job_record_key=key,
                        )
                        .first()
                    )
                    if existing is None:
                        raise
                    result = "idempotent"
                if result != "idempotent":
                    source_accepted += 1
                    source_discovered += 1
                source_created += result == "created"
                source_updated += result == "updated"
                source_duplicates += result == "duplicate"
            source_created += int(previous.get("inserted", previous.get("created", 0)))
            source_updated += int(previous.get("updated", 0))
            source_duplicates += int(previous.get("duplicates", 0))
            source_accepted += int(previous.get("accepted", 0))
            source_discovered += int(previous.get("discovered", 0))
            rejected = len(batch.issues) + int(previous.get("rejected", 0))
            source.last_run_at = now()
            if batch.records or not batch.issues:
                source.last_success_at = now()
            source.last_error = None
            per_source[source.code] = {
                "status": "succeeded",
                "discovered": source_discovered + len(batch.issues),
                "accepted": source_accepted,
                "rejected": rejected,
                "inserted": source_created,
                "created": source_created,
                "updated": source_updated,
                "duplicates": source_duplicates,
                "errors": [
                    {
                        "kind": issue.kind.value,
                        "message": issue.message,
                        "record_index": issue.record_index,
                        "retry_after": issue.retry_after,
                    }
                    for issue in batch.issues
                ],
            }
            if len(records) < len(batch.records):
                per_source[source.code]["truncated"] = True
            if batch.issues:
                per_source[source.code]["status"] = "partial_success" if batch.records else "failed"
                source.last_error = "; ".join(
                    f"{issue.kind.value}: {issue.message}" for issue in batch.issues[:5]
                )[:500]
            else:
                source.last_error = None
        except Exception as exc:
            db.rollback()
            db.refresh(job)
            source = db.get(Source, source.id)
            kind = (
                exc.kind.value
                if isinstance(exc, ConnectorError)
                else ConnectorErrorKind.CONFIGURATION.value
                if isinstance(exc, ValueError)
                else ConnectorErrorKind.INVALID_RESPONSE.value
            )
            source.last_run_at, source.last_error = now(), str(exc)[:500]
            per_source[source.code] = {
                **previous,
                "status": "failed",
                "error": str(exc)[:300],
                "kind": kind,
                "retry_after": exc.retry_after if isinstance(exc, ConnectorError) else None,
            }
            source.last_run_at, source.last_error = now(), str(exc)[:500]
            logger.warning(
                "collection_source_failed",
                extra={
                    "job_id": job.id,
                    "source_code": source.code,
                    "error_kind": kind,
                    "retry_after": exc.retry_after if isinstance(exc, ConnectorError) else None,
                },
            )
        job.outcome = _job_outcome(per_source, job.retry_count)
        db.commit()
    control = _job_control(db, job, lease_token)
    if control != "running":
        db.close()
        return
    job.outcome = _job_outcome(per_source, job.retry_count)
    succeeded = any(
        result.get("status") in {"succeeded", "partial_success"} for result in per_source.values()
    )
    failed = any(
        result.get("status") in {"failed", "partial_success"} for result in per_source.values()
    )
    target = "partial_success" if succeeded and failed else "succeeded" if succeeded else "failed"
    transition_job(job, target)
    job.worker_lease_token = None
    job.error = None if succeeded else "No approved compatible sources succeeded"
    refresh_execution_status(db, job)
    db.commit()
    logger.info(
        "collection_job_finished",
        extra={
            "job_id": job.id,
            "job_type": job.job_type,
            "trigger_type": job.trigger_type,
            "status": job.status,
            "retry_count": job.retry_count,
            "outcome": job.outcome,
        },
    )
    db.close()


def _job_outcome(per_source: dict[str, dict], retry_count: int) -> dict:
    counts = {
        key: sum(int(result.get(key, 0)) for result in per_source.values())
        for key in ("discovered", "accepted", "rejected", "inserted", "updated", "duplicates")
    }
    return {
        "sources": per_source,
        **counts,
        "created": counts["inserted"],
        "retry_count": retry_count,
    }


def _source_has_transient_error(result: dict) -> bool:
    transient = {"timeout", "rate_limited", "temporary_server", "unavailable"}
    if result.get("kind"):
        return result["kind"] in transient
    return any(issue.get("kind") in transient for issue in result.get("errors", []))


def _job_control(db, job, lease_token: str | None = None) -> str:
    db.refresh(job)
    expected_token = lease_token if lease_token is not None else job.worker_lease_token
    if expected_token and job.worker_lease_token != expected_token:
        return "superseded"
    if job.status == "cancel_requested":
        acknowledge_cancellation(job)
        job.worker_lease_token = None
        refresh_execution_status(db, job)
        db.commit()
        logger.info("collection_job_cancelled", extra={"job_id": job.id})
        return "cancelled"
    if job.status != "running":
        return job.status
    started = job.last_attempt_at or job.started_at or now()
    started = started.replace(tzinfo=UTC) if started.tzinfo is None else started
    if (now() - started).total_seconds() > job.execution_timeout_seconds:
        transition_job(job, "failed", error="Maximum collection execution time exceeded")
        job.worker_lease_token = None
        outcome = dict(job.outcome or {})
        sources = dict(outcome.get("sources", {}))
        sources["__worker__"] = {
            "status": "failed",
            "kind": "timeout",
            "error": "Maximum collection execution time exceeded",
        }
        outcome.update(sources=sources, timeout=True)
        job.outcome = outcome
        refresh_execution_status(db, job)
        db.commit()
        logger.warning("collection_job_timed_out", extra={"job_id": job.id})
        return "failed"
    return "running"


def _candidate_job_key(item: Candidate) -> tuple[str, str]:
    raw_values = item.raw_values or item.model_dump(mode="json", exclude={"raw_values"})
    payload_hash = hashlib.sha256(
        json.dumps(raw_values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    source_key = f"external:{item.external_id}" if item.external_id else f"payload:{payload_hash}"
    return hashlib.sha256(source_key.encode()).hexdigest(), payload_hash


def upsert(db, source: Source, item: Candidate, job_id: str | None = None) -> str:
    raw_values = item.raw_values or item.model_dump(mode="json", exclude={"raw_values"})
    job_record_key, payload_hash = _candidate_job_key(item)
    if job_id:
        already_processed = (
            db.query(SourceRecord)
            .filter_by(
                collection_job_id=job_id,
                source_id=source.id,
                job_record_key=job_record_key,
            )
            .first()
        )
        if already_processed:
            return "idempotent"
    match = None
    classification = MatchClass.DISTINCT
    rule = "no_candidate"
    score = 0.0
    if item.external_id:
        existing_record = (
            db.query(SourceRecord)
            .filter_by(source_id=source.id, external_id=item.external_id)
            .order_by(SourceRecord.collected_at.asc())
            .first()
        )
        if existing_record:
            business = existing_record.business
            classification, rule, score = MatchClass.EXACT, "source_external_id", 1.0
            outcome = "duplicate"
        else:
            business = None
    else:
        existing_record = (
            db.query(SourceRecord)
            .filter_by(source_id=source.id, payload_hash=payload_hash)
            .order_by(SourceRecord.collected_at.asc())
            .first()
        )
        if existing_record:
            business = existing_record.business
            classification, rule, score = MatchClass.EXACT, "source_payload_fingerprint", 1.0
            outcome = "duplicate"
        else:
            business = None
    if business is None:
        candidates = (
            db.query(Business).filter(Business.country == item.country).order_by(Business.id).all()
        )
        rules = MatchingRules.from_config(source.config.get("matching_rules"))
        match = best_match(candidates, item, rules)
        classification, rule, score = match.classification, match.rule, match.score
        if classification == MatchClass.EXACT and match.candidate:
            business = match.candidate
            outcome = "updated"
            _update_business(business, item, source)
        else:
            business = _new_business(item, source)
            db.add(business)
            db.flush()
            outcome = "created"
    else:
        _update_business(business, item, source)
    business.last_seen = now()
    business.updated_at = now()
    record = SourceRecord(
        source_id=source.id,
        source_identifier=source.code,
        source_name=source.name,
        business_id=business.id,
        collection_job_id=job_id,
        business_disposition="new" if outcome == "created" else "existing",
        job_record_key=job_record_key if job_id else None,
        external_id=item.external_id,
        source_url=item.source_url,
        collection_status="collected",
        metadata_json={"attribution": source.attribution or ""},
        raw_values_json=raw_values,
        payload_hash=payload_hash,
    )
    db.add(record)
    db.flush()
    db.add(
        MatchDecision(
            source_record_id=record.id,
            business_id=business.id,
            candidate_business_id=(
                match.candidate.id
                if match and match.candidate
                else business.id
                if classification == MatchClass.EXACT
                else None
            ),
            classification=classification.value,
            rule=rule,
            score=score,
            auto_merged=classification == MatchClass.EXACT and outcome != "created",
        )
    )
    db.flush()
    return outcome


def _field_quality(source: Source, item: Candidate) -> dict[str, int]:
    configured = source.config.get("field_quality", {})
    quality = {}
    for field in (
        "name",
        "website",
        "city",
        "region",
        "address",
        "postal_code",
        "latitude",
        "longitude",
        "categories",
        "email",
        "phone",
        "description",
    ):
        value = getattr(item, field)
        if value is None or value == "" or value == []:
            continue
        try:
            score = int(configured.get(field, 50))
        except (TypeError, ValueError):
            score = 50
        quality[field] = min(100, max(0, score))
    return quality


def _new_business(item: Candidate, source: Source) -> Business:
    latitude, longitude = normalize_coordinates(item.latitude, item.longitude)
    return Business(
        name=item.name.strip(),
        normalized_name=normalize_name(item.name),
        domain=normalize_domain(item.website),
        normalized_address=normalize_address(item.address),
        normalized_phone=normalize_phone(item.phone),
        field_quality_json=_field_quality(source, item),
        website=item.website.strip() if item.website else None,
        country=item.country.upper(),
        city=item.city.strip() if item.city else None,
        region=item.region.strip() if item.region else None,
        address=item.address.strip() if item.address else None,
        postal_code=item.postal_code.strip() if item.postal_code else None,
        latitude=latitude,
        longitude=longitude,
        categories=item.categories,
        email=item.email.strip().lower() if item.email else None,
        phone=item.phone.strip() if item.phone else None,
        description=item.description.strip() if item.description else None,
        synthetic=item.synthetic,
    )


def _update_business(business: Business, item: Candidate, source: Source) -> None:
    latitude, longitude = normalize_coordinates(item.latitude, item.longitude)
    incoming = {
        "name": item.name.strip(),
        "website": item.website.strip() if item.website else None,
        "address": item.address.strip() if item.address else None,
        "city": item.city.strip() if item.city else None,
        "region": item.region.strip() if item.region else None,
        "postal_code": item.postal_code.strip() if item.postal_code else None,
        "latitude": latitude,
        "longitude": longitude,
        "categories": item.categories or None,
        "email": item.email.strip().lower() if item.email else None,
        "phone": item.phone.strip() if item.phone else None,
        "description": item.description.strip() if item.description else None,
    }
    incoming_quality = _field_quality(source, item)
    quality = dict(business.field_quality_json or {})
    for field, value in incoming.items():
        if value is None or value == "":
            continue
        existing = getattr(business, field)
        new_score = incoming_quality.get(field, 50)
        old_score = quality.get(field, 50 if existing else -1)
        if not existing or new_score > old_score:
            setattr(business, field, value)
            quality[field] = new_score
    business.normalized_name = normalize_name(business.name)
    business.domain = normalize_domain(business.website)
    business.normalized_address = normalize_address(business.address)
    business.normalized_phone = normalize_phone(business.phone)
    business.field_quality_json = quality
