import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.db.models import Base, Business, Source, SourceRecord


def test_migrations_upgrade_clean_sqlite_database(tmp_path):
    database_path = tmp_path / "migration.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    inspector = inspect(create_engine(environment["DATABASE_URL"]))
    assert {"businesses", "sources", "source_records"}.issubset(set(inspector.get_table_names()))
    assert {"website", "verified_at", "created_at", "updated_at"}.issubset(
        {column["name"] for column in inspector.get_columns("businesses")}
    )
    assert {
        "source_identifier",
        "source_name",
        "first_seen",
        "last_seen",
        "collection_status",
    }.issubset({column["name"] for column in inspector.get_columns("source_records")})
    assert "bounds" in {column["name"] for column in inspector.get_columns("jobs")}
    assert "region" in {column["name"] for column in inspector.get_columns("jobs")}
    assert {"collection_job_id", "business_disposition"}.issubset(
        {column["name"] for column in inspector.get_columns("source_records")}
    )
    assert {"normalized_address", "normalized_phone", "field_quality_json"}.issubset(
        {column["name"] for column in inspector.get_columns("businesses")}
    )
    assert {"raw_values_json", "payload_hash"}.issubset(
        {column["name"] for column in inspector.get_columns("source_records")}
    )
    assert {"match_decisions", "business_merge_events"}.issubset(set(inspector.get_table_names()))
    assert "schedule_executions" in inspector.get_table_names()
    assert {
        "countries",
        "region",
        "city",
        "category",
        "bounds",
        "max_results",
        "source_limits",
        "allow_overlap",
        "retry_attempts",
        "last_error",
        "archived",
    }.issubset({column["name"] for column in inspector.get_columns("schedules")})
    assert {
        "schedule_id",
        "schedule_execution_id",
        "source_limits",
        "retry_limit",
        "retry_count",
    }.issubset({column["name"] for column in inspector.get_columns("jobs")})


def test_matching_migration_upgrades_existing_phase4_schema_and_backfills_keys(tmp_path):
    database_path = tmp_path / "phase4.db"
    url = f"sqlite:///{database_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        source = Source(code="legacy", name="Legacy", source_type="test", connector="dataset")
        session.add(source)
        session.flush()
        business = Business(
            name="Café Books",
            normalized_name="old normalizer",
            domain="www.cafe.example",
            website="https://WWW.Cafe.example/",
            country="US",
            address=" 10 Café St ",
            phone="+1 (415) 555-0100",
        )
        session.add(business)
        session.flush()
        session.add(
            SourceRecord(
                source_id=source.id,
                source_identifier=source.code,
                source_name=source.name,
                business_id=business.id,
                external_id="legacy-1",
            )
        )
        session.commit()
    with engine.begin() as connection:
        for table in ("business_merge_events", "match_decisions"):
            connection.exec_driver_sql(f"DROP TABLE {table}")
        connection.exec_driver_sql("DROP INDEX ix_businesses_normalized_address")
        connection.exec_driver_sql("DROP INDEX ix_businesses_normalized_phone")
        for column in ("normalized_address", "normalized_phone", "field_quality_json"):
            connection.exec_driver_sql(f"ALTER TABLE businesses DROP COLUMN {column}")
        connection.exec_driver_sql("ALTER TABLE source_records DROP COLUMN raw_values_json")
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('0003_job_bounds')"
        )
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )
    from sqlalchemy import text

    with engine.connect() as connection:
        row = (
            connection.execute(text("SELECT * FROM businesses WHERE name='Café Books'"))
            .mappings()
            .one()
        )
        assert row["normalized_name"] == "cafe books"
        assert row["domain"] == "cafe.example"
        assert row["normalized_address"] == "10 cafe st"
        assert row["normalized_phone"] == "+14155550100"
