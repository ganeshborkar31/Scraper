"""Explicit canonical-business merges with reversible audit events."""

from datetime import datetime

from sqlalchemy import DateTime, select

from app.db.models import Business, BusinessMergeEvent, MatchDecision, SourceRecord, now
from app.services.normalization import (
    normalize_address,
    normalize_domain,
    normalize_name,
    normalize_phone,
)


class MergeError(ValueError):
    pass


def _snapshot(business: Business) -> dict:
    result = {}
    for column in Business.__table__.columns:
        value = getattr(business, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def merge_businesses(
    db, source_business_id: str, target_business_id: str, actor: str | None = None
) -> BusinessMergeEvent:
    if source_business_id == target_business_id:
        raise MergeError("source and target businesses must differ")
    source = db.get(Business, source_business_id)
    target = db.get(Business, target_business_id)
    if not source or not target:
        raise MergeError("source or target business was not found")
    records = db.scalars(
        select(SourceRecord).where(SourceRecord.business_id == source_business_id)
    ).all()
    changes: dict[str, dict] = {}
    source_quality = source.field_quality_json or {}
    target_quality = dict(target.field_quality_json or {})
    quality_before = dict(target_quality)
    merge_fields = (
        "name",
        "website",
        "address",
        "city",
        "region",
        "postal_code",
        "latitude",
        "longitude",
        "categories",
        "email",
        "phone",
        "description",
    )
    for field in merge_fields:
        source_value = getattr(source, field)
        target_value = getattr(target, field)
        if source_value in (None, "", []):
            continue
        source_score = source_quality.get(field, 50)
        target_score = target_quality.get(field, 50 if target_value not in (None, "", []) else -1)
        if target_value in (None, "", []) or source_score > target_score:
            changes[field] = {"before": target_value, "after": source_value}
            setattr(target, field, source_value)
            target_quality[field] = source_score
    target.field_quality_json = target_quality
    target.normalized_name = normalize_name(target.name)
    target.domain = normalize_domain(target.website)
    target.normalized_address = normalize_address(target.address)
    target.normalized_phone = normalize_phone(target.phone)
    event = BusinessMergeEvent(
        source_business_id=source_business_id,
        target_business_id=target_business_id,
        source_snapshot=_snapshot(source),
        source_record_ids=[record.id for record in records],
        target_changes_json={
            "fields": changes,
            "quality_before": quality_before,
            "quality_after": target_quality,
        },
        actor=actor,
    )
    db.add(event)
    db.flush()
    for record in records:
        record.business_id = target_business_id
        decision = db.scalar(
            select(MatchDecision).where(MatchDecision.source_record_id == record.id)
        )
        if decision:
            decision.business_id = target_business_id
    db.flush()
    db.delete(source)
    db.flush()
    return event


def reverse_business_merge(db, merge_id: str, actor: str | None = None) -> Business:
    event = db.get(BusinessMergeEvent, merge_id)
    if not event:
        raise MergeError("merge event was not found")
    if event.reversed_at:
        raise MergeError("merge event was already reversed")
    if not db.get(Business, event.target_business_id):
        raise MergeError("merge target no longer exists")
    if db.get(Business, event.source_business_id):
        raise MergeError("original business ID is already in use")
    values = dict(event.source_snapshot)
    for column in Business.__table__.columns:
        if isinstance(column.type, DateTime):
            value = values.get(column.name)
            if value:
                values[column.name] = datetime.fromisoformat(value)
    restored = Business(**values)
    db.add(restored)
    db.flush()
    target = db.get(Business, event.target_business_id)
    target_changes = event.target_changes_json or {}
    for field, change in target_changes.get("fields", {}).items():
        if getattr(target, field) == change.get("after"):
            setattr(target, field, change.get("before"))
    quality_after = target_changes.get("quality_after", {})
    quality_before = target_changes.get("quality_before", {})
    target_quality = dict(target.field_quality_json or {})
    for field, after_score in quality_after.items():
        if target_quality.get(field) == after_score:
            if field in quality_before:
                target_quality[field] = quality_before[field]
            else:
                target_quality.pop(field, None)
    target.field_quality_json = target_quality
    target.normalized_name = normalize_name(target.name)
    target.domain = normalize_domain(target.website)
    target.normalized_address = normalize_address(target.address)
    target.normalized_phone = normalize_phone(target.phone)
    for record_id in event.source_record_ids:
        record = db.get(SourceRecord, record_id)
        if record:
            record.business_id = restored.id
            decision = db.scalar(
                select(MatchDecision).where(MatchDecision.source_record_id == record.id)
            )
            if decision:
                decision.business_id = restored.id
    event.reversed_at = now()
    event.reversed_by = actor
    db.flush()
    return restored
