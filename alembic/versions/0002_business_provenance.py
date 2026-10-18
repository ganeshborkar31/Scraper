"""Expand business attributes and preserve repeat source observations."""

import sqlalchemy as sa

from alembic import op

revision = "0002_business_provenance"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 0001 used live metadata, so a fresh install may already have these columns.
    business_columns = _columns("businesses")
    for name, column in (
        ("website", sa.Column("website", sa.String(1000))),
        ("verified_at", sa.Column("verified_at", sa.DateTime(timezone=True))),
        ("created_at", sa.Column("created_at", sa.DateTime(timezone=True))),
        ("updated_at", sa.Column("updated_at", sa.DateTime(timezone=True))),
    ):
        if name not in business_columns:
            op.add_column("businesses", column)
    op.execute("UPDATE businesses SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    op.execute("UPDATE businesses SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")

    source_columns = _columns("source_records")
    for name, column in (
        ("source_identifier", sa.Column("source_identifier", sa.String(80))),
        ("source_name", sa.Column("source_name", sa.String(200))),
        ("first_seen", sa.Column("first_seen", sa.DateTime(timezone=True))),
        ("last_seen", sa.Column("last_seen", sa.DateTime(timezone=True))),
        ("collection_status", sa.Column("collection_status", sa.String(32))),
    ):
        if name not in source_columns:
            op.add_column("source_records", column)
    op.execute(
        """UPDATE source_records SET
        source_identifier = (SELECT code FROM sources WHERE sources.id = source_records.source_id),
        source_name = (SELECT name FROM sources WHERE sources.id = source_records.source_id),
        first_seen = COALESCE(collected_at, CURRENT_TIMESTAMP),
        last_seen = COALESCE(collected_at, CURRENT_TIMESTAMP),
        collection_status = COALESCE(collection_status, 'collected')
        WHERE source_identifier IS NULL OR source_name IS NULL OR first_seen IS NULL
           OR last_seen IS NULL OR collection_status IS NULL"""
    )
    # Legacy uq_source_external prevented repeat observations; drop it so every
    # collection can retain its own attributable record.
    inspector = sa.inspect(op.get_bind())
    legacy_unique = next(
        (
            constraint["name"]
            for constraint in inspector.get_unique_constraints("source_records")
            if constraint.get("column_names") == ["source_id", "external_id"]
        ),
        None,
    )
    if legacy_unique:
        with op.batch_alter_table("source_records") as batch:
            batch.drop_constraint(legacy_unique, type_="unique")
    source_indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("source_records")}
    business_indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("businesses")}
    if "ix_source_records_source_external" not in source_indexes:
        op.create_index(
            "ix_source_records_source_external", "source_records", ["source_id", "external_id"]
        )
    if "ix_source_records_seen" not in source_indexes:
        op.create_index("ix_source_records_seen", "source_records", ["first_seen", "last_seen"])
    if "ix_business_country_city" not in business_indexes:
        op.create_index("ix_business_country_city", "businesses", ["country", "city"])
    existing_checks = {c["name"] for c in inspector.get_check_constraints("businesses")}
    with op.batch_alter_table("businesses") as batch:
        if "ck_business_latitude" not in existing_checks:
            batch.create_check_constraint(
                "ck_business_latitude", "latitude IS NULL OR latitude BETWEEN -90 AND 90"
            )
        if "ck_business_longitude" not in existing_checks:
            batch.create_check_constraint(
                "ck_business_longitude", "longitude IS NULL OR longitude BETWEEN -180 AND 180"
            )


def downgrade() -> None:
    op.drop_constraint("ck_business_longitude", "businesses", type_="check")
    op.drop_constraint("ck_business_latitude", "businesses", type_="check")
    op.drop_index("ix_business_country_city", table_name="businesses")
    op.drop_index("ix_source_records_seen", table_name="source_records")
    op.drop_index("ix_source_records_source_external", table_name="source_records")
    op.create_unique_constraint(
        "uq_source_external", "source_records", ["source_id", "external_id"]
    )
    for name in (
        "collection_status",
        "last_seen",
        "first_seen",
        "source_name",
        "source_identifier",
    ):
        op.drop_column("source_records", name)
    for name in ("updated_at", "created_at", "verified_at", "website"):
        op.drop_column("businesses", name)
