"""Store normalized keys, raw observations, match decisions, and reversible merges."""

import re
import unicodedata
from urllib.parse import urlparse

import sqlalchemy as sa

from alembic import op

revision = "0004_matching_audit"
down_revision = "0003_job_bounds"
branch_labels = None
depends_on = None


def _fold(value: str | None) -> str | None:
    if not value:
        return None
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join("".join(char if char.isalnum() else " " for char in plain).split()) or None


def _domain(value: str | None, existing: str | None) -> str | None:
    if not value:
        return existing
    try:
        host = urlparse(value if "://" in value else f"https://{value}").hostname
        host = host.encode("idna").decode("ascii").lower() if host else None
    except (UnicodeError, ValueError):
        return existing
    if host:
        host = host.removeprefix("www.").rstrip(".")
    return host or existing


def _phone(value: str | None) -> str | None:
    if not value:
        return None
    extension_match = re.search(r"(?:ext\.?|x)\s*(\d+)\s*$", value, re.IGNORECASE)
    extension = extension_match.group(1) if extension_match else None
    main = value[: extension_match.start()] if extension_match else value
    digits = "".join(char for char in main if char.isdigit())
    if not digits:
        return None
    international = main.strip().startswith("+") or digits.startswith("00")
    digits = digits.removeprefix("00")
    return f"{'+' if international else ''}{digits}{f'x{extension}' if extension else ''}"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    business_columns = {column["name"] for column in inspector.get_columns("businesses")}
    for name, column in (
        ("normalized_address", sa.Column("normalized_address", sa.String(1000))),
        ("normalized_phone", sa.Column("normalized_phone", sa.String(64))),
        (
            "field_quality_json",
            sa.Column("field_quality_json", sa.JSON(), nullable=False, server_default="{}"),
        ),
    ):
        if name not in business_columns:
            op.add_column("businesses", column)
    source_record_columns = {column["name"] for column in inspector.get_columns("source_records")}
    if "raw_values_json" not in source_record_columns:
        op.add_column(
            "source_records",
            sa.Column("raw_values_json", sa.JSON(), nullable=False, server_default="{}"),
        )
    businesses = sa.table(
        "businesses",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("normalized_name", sa.String),
        sa.column("website", sa.String),
        sa.column("domain", sa.String),
        sa.column("normalized_address", sa.String),
        sa.column("normalized_phone", sa.String),
        sa.column("field_quality_json", sa.JSON),
        sa.column("address", sa.String),
        sa.column("phone", sa.String),
        sa.column("latitude", sa.Float),
        sa.column("longitude", sa.Float),
        sa.column("categories", sa.JSON),
        sa.column("email", sa.String),
        sa.column("city", sa.String),
        sa.column("region", sa.String),
        sa.column("postal_code", sa.String),
        sa.column("description", sa.String),
    )
    for row in bind.execute(sa.select(businesses)).mappings():
        quality = {
            field: 50
            for field in (
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
            if row[field] not in (None, "", [])
        }
        latitude = round(row["latitude"], 6) if row["latitude"] is not None else None
        longitude = round(row["longitude"], 6) if row["longitude"] is not None else None
        bind.execute(
            sa.update(businesses)
            .where(businesses.c.id == row["id"])
            .values(
                normalized_name=_fold(row["name"]) or "",
                domain=_domain(row["website"], row["domain"]),
                normalized_address=_fold(row["address"]),
                normalized_phone=_phone(row["phone"]),
                latitude=latitude,
                longitude=longitude,
                field_quality_json=quality,
            )
        )
    table_names = set(inspector.get_table_names())
    if "match_decisions" not in table_names:
        op.create_table(
            "match_decisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "source_record_id",
                sa.String(36),
                sa.ForeignKey("source_records.id"),
                unique=True,
                nullable=False,
            ),
            sa.Column("business_id", sa.String(36), nullable=False),
            sa.Column("candidate_business_id", sa.String(36)),
            sa.Column("classification", sa.String(20), nullable=False),
            sa.Column("rule", sa.String(80), nullable=False),
            sa.Column("score", sa.Float(), nullable=False, server_default="1"),
            sa.Column("auto_merged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "business_merge_events" not in table_names:
        op.create_table(
            "business_merge_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_business_id", sa.String(36), nullable=False),
            sa.Column("target_business_id", sa.String(36), nullable=False),
            sa.Column("source_snapshot", sa.JSON(), nullable=False),
            sa.Column("source_record_ids", sa.JSON(), nullable=False),
            sa.Column("target_changes_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("actor", sa.String(200)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reversed_at", sa.DateTime(timezone=True)),
            sa.Column("reversed_by", sa.String(200)),
        )
    merge_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("business_merge_events")
    }
    if "target_changes_json" not in merge_columns:
        op.add_column(
            "business_merge_events",
            sa.Column("target_changes_json", sa.JSON(), nullable=False, server_default="{}"),
        )
    if "actor" not in merge_columns:
        op.add_column("business_merge_events", sa.Column("actor", sa.String(200)))
    if "reversed_by" not in merge_columns:
        op.add_column("business_merge_events", sa.Column("reversed_by", sa.String(200)))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("businesses")}
    for name, column in (
        ("ix_businesses_normalized_address", "normalized_address"),
        ("ix_businesses_normalized_phone", "normalized_phone"),
    ):
        if name not in indexes:
            op.create_index(name, "businesses", [column])
    merge_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("business_merge_events")
    }
    for name, column in (
        ("ix_business_merge_events_source_business_id", "source_business_id"),
        ("ix_business_merge_events_target_business_id", "target_business_id"),
    ):
        if name not in merge_indexes:
            op.create_index(name, "business_merge_events", [column])


def downgrade() -> None:
    op.drop_index("ix_business_merge_events_target_business_id", table_name="business_merge_events")
    op.drop_index("ix_business_merge_events_source_business_id", table_name="business_merge_events")
    op.drop_table("business_merge_events")
    op.drop_table("match_decisions")
    op.drop_index("ix_businesses_normalized_phone", table_name="businesses")
    op.drop_index("ix_businesses_normalized_address", table_name="businesses")
    op.drop_column("source_records", "raw_values_json")
    op.drop_column("businesses", "field_quality_json")
    op.drop_column("businesses", "normalized_phone")
    op.drop_column("businesses", "normalized_address")
