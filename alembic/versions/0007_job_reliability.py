"""Add job leases, cancellation metadata, and per-job record idempotency."""

import sqlalchemy as sa

from alembic import op

revision = "0007_job_reliability"
down_revision = "0006_scheduled_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    job_columns = {column["name"] for column in sa.inspect(bind).get_columns("jobs")}
    for name, column in (
        (
            "job_type",
            sa.Column("job_type", sa.String(40), nullable=False, server_default="collection"),
        ),
        (
            "trigger_type",
            sa.Column("trigger_type", sa.String(24), nullable=False, server_default="manual"),
        ),
        (
            "execution_timeout_seconds",
            sa.Column(
                "execution_timeout_seconds", sa.Integer(), nullable=False, server_default="1800"
            ),
        ),
        ("worker_lease_token", sa.Column("worker_lease_token", sa.String(36))),
        ("last_attempt_at", sa.Column("last_attempt_at", sa.DateTime(timezone=True))),
        ("last_enqueued_at", sa.Column("last_enqueued_at", sa.DateTime(timezone=True))),
        (
            "cancellation_requested_at",
            sa.Column("cancellation_requested_at", sa.DateTime(timezone=True)),
        ),
    ):
        if name not in job_columns:
            op.add_column("jobs", column)
    bind.execute(
        sa.text("UPDATE jobs SET last_enqueued_at = created_at WHERE last_enqueued_at IS NULL")
    )
    bind.execute(
        sa.text(
            "UPDATE jobs SET last_attempt_at = started_at "
            "WHERE status = 'running' AND last_attempt_at IS NULL"
        )
    )

    record_columns = {column["name"] for column in sa.inspect(bind).get_columns("source_records")}
    if "job_record_key" not in record_columns:
        op.add_column("source_records", sa.Column("job_record_key", sa.String(64)))
    records = sa.table(
        "source_records",
        sa.column("id", sa.String(36)),
        sa.column("source_id", sa.String(36)),
        sa.column("external_id", sa.String(300)),
        sa.column("payload_hash", sa.String(64)),
        sa.column("job_record_key", sa.String(64)),
    )
    for row in bind.execute(
        sa.select(records.c.id, records.c.external_id, records.c.payload_hash).where(
            records.c.job_record_key.is_(None)
        )
    ).mappings():
        # Existing observations retain identity; new jobs use deterministic candidate hashes.
        value = f"legacy:{row['id']}"
        bind.execute(
            sa.update(records).where(records.c.id == row["id"]).values(job_record_key=value)
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("source_records")}
    constraints = {
        constraint["name"]
        for constraint in sa.inspect(bind).get_unique_constraints("source_records")
    }
    if "uq_source_record_job_key" not in indexes | constraints:
        op.create_index(
            "uq_source_record_job_key",
            "source_records",
            ["collection_job_id", "source_id", "job_record_key"],
            unique=True,
        )
    job_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("jobs")}
    for name, column in (
        ("ix_jobs_last_attempt_at", "last_attempt_at"),
        ("ix_jobs_last_enqueued_at", "last_enqueued_at"),
    ):
        if name not in job_indexes:
            op.create_index(name, "jobs", [column])


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("source_records")}
    if "uq_source_record_job_key" in indexes:
        op.drop_index("uq_source_record_job_key", table_name="source_records")
    op.drop_index("ix_jobs_last_enqueued_at", table_name="jobs")
    op.drop_index("ix_jobs_last_attempt_at", table_name="jobs")
    op.drop_column("source_records", "job_record_key")
    for name in (
        "cancellation_requested_at",
        "last_enqueued_at",
        "last_attempt_at",
        "execution_timeout_seconds",
        "worker_lease_token",
        "trigger_type",
        "job_type",
    ):
        op.drop_column("jobs", name)
