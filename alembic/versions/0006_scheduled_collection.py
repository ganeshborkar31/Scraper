"""Add configurable recurring collection and execution history."""

import sqlalchemy as sa

from alembic import op

revision = "0006_scheduled_collection"
down_revision = "0005_on_demand_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    schedule_columns = {column["name"] for column in inspector.get_columns("schedules")}
    schedule_additions = {
        "countries": sa.Column("countries", sa.JSON(), nullable=False, server_default="[]"),
        "region": sa.Column("region", sa.String(200)),
        "city": sa.Column("city", sa.String(200)),
        "category": sa.Column("category", sa.String(200)),
        "bounds": sa.Column("bounds", sa.JSON()),
        "max_results": sa.Column("max_results", sa.Integer(), nullable=False, server_default="100"),
        "source_limits": sa.Column("source_limits", sa.JSON(), nullable=False, server_default="{}"),
        "allow_overlap": sa.Column(
            "allow_overlap", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        "archived": sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        "retry_attempts": sa.Column(
            "retry_attempts", sa.Integer(), nullable=False, server_default="3"
        ),
        "last_error": sa.Column("last_error", sa.Text()),
    }
    for name, column in schedule_additions.items():
        if name not in schedule_columns:
            op.add_column("schedules", column)

    tables = set(sa.inspect(bind).get_table_names())
    if "schedule_executions" not in tables:
        op.create_table(
            "schedule_executions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("schedule_id", sa.String(36), sa.ForeignKey("schedules.id"), nullable=False),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("job_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("error", sa.Text()),
        )

    job_columns = {column["name"] for column in sa.inspect(bind).get_columns("jobs")}
    for name, column in (
        (
            "source_limits",
            sa.Column("source_limits", sa.JSON(), nullable=False, server_default="{}"),
        ),
        ("schedule_id", sa.Column("schedule_id", sa.String(36), sa.ForeignKey("schedules.id"))),
        (
            "schedule_execution_id",
            sa.Column(
                "schedule_execution_id", sa.String(36), sa.ForeignKey("schedule_executions.id")
            ),
        ),
        ("retry_limit", sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="3")),
        ("retry_count", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0")),
    ):
        if name not in job_columns:
            op.add_column("jobs", column)

    indexes = {
        table: {index["name"] for index in sa.inspect(bind).get_indexes(table)}
        for table in ("jobs", "schedule_executions")
    }
    for table, name, column in (
        ("jobs", "ix_jobs_schedule_id", "schedule_id"),
        ("jobs", "ix_jobs_schedule_execution_id", "schedule_execution_id"),
        ("schedule_executions", "ix_schedule_executions_schedule_id", "schedule_id"),
        ("schedule_executions", "ix_schedule_executions_scheduled_for", "scheduled_for"),
    ):
        if name not in indexes.get(table, set()):
            op.create_index(name, table, [column])


def downgrade() -> None:
    op.drop_index("ix_schedule_executions_scheduled_for", table_name="schedule_executions")
    op.drop_index("ix_schedule_executions_schedule_id", table_name="schedule_executions")
    op.drop_index("ix_jobs_schedule_execution_id", table_name="jobs")
    op.drop_index("ix_jobs_schedule_id", table_name="jobs")
    op.drop_table("schedule_executions")
    for name in (
        "retry_count",
        "retry_limit",
        "schedule_execution_id",
        "schedule_id",
        "source_limits",
    ):
        op.drop_column("jobs", name)
    for name in (
        "last_error",
        "archived",
        "retry_attempts",
        "allow_overlap",
        "source_limits",
        "max_results",
        "bounds",
        "category",
        "city",
        "region",
        "countries",
    ):
        op.drop_column("schedules", name)
