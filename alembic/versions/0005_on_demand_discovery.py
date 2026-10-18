"""Persist discovery regions and connect collected source records to jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0005_on_demand_discovery"
down_revision = "0004_matching_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "region" not in job_columns:
        op.add_column("jobs", sa.Column("region", sa.String(200), nullable=True))
    record_columns = {column["name"] for column in inspector.get_columns("source_records")}
    if "collection_job_id" not in record_columns:
        op.add_column(
            "source_records",
            sa.Column("collection_job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
        )
    if "business_disposition" not in record_columns:
        op.add_column(
            "source_records", sa.Column("business_disposition", sa.String(16), nullable=True)
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("source_records")}
    if "ix_source_records_collection_job_id" not in indexes:
        op.create_index(
            "ix_source_records_collection_job_id", "source_records", ["collection_job_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_source_records_collection_job_id", table_name="source_records")
    op.drop_column("source_records", "business_disposition")
    op.drop_column("source_records", "collection_job_id")
    op.drop_column("jobs", "region")
