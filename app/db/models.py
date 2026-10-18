import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def now() -> datetime:
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(40))
    connector: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(32), default="pending_review")
    countries: Mapped[list] = mapped_column(JSON, default=list)
    record_types: Mapped[list] = mapped_column(JSON, default=lambda: ["business"])
    docs_url: Mapped[str | None] = mapped_column(String(500))
    terms_url: Mapped[str | None] = mapped_column(String(500))
    license_info: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_business_latitude"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name="ck_business_longitude"
        ),
        Index("ix_business_country_city", "country", "city"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(300), index=True)
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    normalized_address: Mapped[str | None] = mapped_column(String(1000), index=True)
    normalized_phone: Mapped[str | None] = mapped_column(String(64), index=True)
    field_quality_json: Mapped[dict] = mapped_column(JSON, default=dict)
    website: Mapped[str | None] = mapped_column(String(1000))
    country: Mapped[str] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(String(32))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    categories: Mapped[list] = mapped_column(JSON, default=list)
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    source_records: Mapped[list["SourceRecord"]] = relationship(back_populates="business")


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (
        Index("ix_source_records_source_external", "source_id", "external_id"),
        Index("ix_source_records_seen", "first_seen", "last_seen"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_identifier: Mapped[str] = mapped_column(String(80))
    source_name: Mapped[str] = mapped_column(String(200))
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    collection_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    business_disposition: Mapped[str | None] = mapped_column(String(16))
    collection_status: Mapped[str] = mapped_column(String(32), default="collected")
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_values_json: Mapped[dict] = mapped_column(JSON, default=dict)
    business: Mapped[Business] = relationship(back_populates="source_records")


class MatchDecision(Base):
    __tablename__ = "match_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_record_id: Mapped[str] = mapped_column(ForeignKey("source_records.id"), unique=True)
    business_id: Mapped[str] = mapped_column(String(36))
    candidate_business_id: Mapped[str | None] = mapped_column(String(36))
    classification: Mapped[str] = mapped_column(String(20))
    rule: Mapped[str] = mapped_column(String(80))
    score: Mapped[float] = mapped_column(default=1.0)
    auto_merged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BusinessMergeEvent(Base):
    __tablename__ = "business_merge_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_business_id: Mapped[str] = mapped_column(String(36), index=True)
    target_business_id: Mapped[str] = mapped_column(String(36), index=True)
    source_snapshot: Mapped[dict] = mapped_column(JSON)
    source_record_ids: Mapped[list] = mapped_column(JSON)
    target_changes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[str | None] = mapped_column(String(200))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query: Mapped[str] = mapped_column(String(500))
    country: Mapped[str] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    bounds: Mapped[dict | None] = mapped_column(JSON)
    max_results: Mapped[int] = mapped_column(Integer, default=100)
    source_limits: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id"), index=True)
    schedule_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_executions.id"), index=True
    )
    retry_limit: Mapped[int] = mapped_column(Integer, default=3)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    query: Mapped[str] = mapped_column(String(500))
    country: Mapped[str] = mapped_column(String(2))
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    region: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    bounds: Mapped[dict | None] = mapped_column(JSON)
    max_results: Mapped[int] = mapped_column(Integer, default=100)
    source_limits: Mapped[dict] = mapped_column(JSON, default=dict)
    allow_overlap: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_attempts: Mapped[int] = mapped_column(Integer, default=3)
    interval_minutes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ScheduleExecution(Base):
    __tablename__ = "schedule_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id"), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
