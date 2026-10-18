"""Persist bounded geographic search parameters on collection jobs."""

import sqlalchemy as sa

from alembic import op

revision = "0003_job_bounds"
down_revision = "0002_business_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    if "bounds" not in columns:
        op.add_column("jobs", sa.Column("bounds", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    if "bounds" in columns:
        op.drop_column("jobs", "bounds")
