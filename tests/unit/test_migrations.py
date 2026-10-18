import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect


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
    assert {"source_identifier", "source_name", "first_seen", "last_seen", "collection_status"}.issubset(
        {column["name"] for column in inspector.get_columns("source_records")}
    )
    assert "bounds" in {column["name"] for column in inspector.get_columns("jobs")}
