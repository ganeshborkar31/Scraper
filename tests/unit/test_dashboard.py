from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db.models import Base, Business, Job, MatchDecision, Source, SourceRecord
from app.schemas import ScheduleRequest, SourceUpdate


def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_dashboard_asset_contains_all_sections_and_no_fixture_records():
    response = main.dashboard(_="admin")
    html = Path(response.path).read_text()
    assert response.media_type == "text/html"
    for section in ("Overview", "Business Explorer", "Sources", "Jobs", "Automatic Collection"):
        assert section in html
    assert "/static/dashboard.js" in html
    assert "Test Shop" not in html


def test_dashboard_and_data_routes_keep_existing_admin_dependency():
    protected_paths = {"/", "/dashboard/overview", "/businesses", "/sources", "/jobs", "/schedules"}
    protected_routes = {
        route.path: route
        for route in main.app.routes
        if getattr(route, "path", None) in protected_paths
    }
    assert protected_paths.issubset(protected_routes)
    for route in protected_routes.values():
        assert any(dependency.call is main.admin for dependency in route.dependant.dependencies)
    with pytest.raises(HTTPException) as error:
        main.admin(HTTPBasicCredentials(username="wrong", password="wrong"))
    assert error.value.status_code == 401


def test_overview_uses_persisted_metrics_jobs_and_provenance():
    factory = database()
    db = factory()
    source = Source(
        code="dataset",
        name="Local sample",
        source_type="open_dataset",
        connector="dataset",
        config={"path": "app/fixtures/synthetic_businesses.json"},
        enabled=True,
        review_status="approved",
    )
    business = Business(
        name="Actual Collected Cafe",
        normalized_name="actual collected cafe",
        country="US",
        city="Oakland",
        categories=["cafe"],
    )
    job = Job(
        query="cafe",
        country="US",
        status="failed",
        outcome={"discovered": 2, "accepted": 1, "rejected": 1, "inserted": 1},
        error="Source temporarily failed",
    )
    db.add_all([source, business, job])
    db.flush()
    record = SourceRecord(
        source_id=source.id,
        source_identifier=source.code,
        source_name=source.name,
        business_id=business.id,
        source_url="https://example.org/cafe",
        raw_values_json={"name": "Actual Collected Cafe"},
    )
    db.add(record)
    db.flush()
    db.add(
        MatchDecision(
            source_record_id=record.id,
            business_id=business.id,
            classification="distinct",
            rule="no_candidate",
            score=0,
        )
    )
    db.commit()

    overview = main.dashboard_overview(_="admin", db=db)
    assert overview["business_count"] == 1
    assert overview["source_count"] == 1
    assert overview["enabled_source_count"] == 1
    assert overview["job_counts"]["failed"] == 1
    assert overview["recent_failures"][0]["error"] == "Source temporarily failed"
    assert overview["recent_activity"][0]["business_name"] == "Actual Collected Cafe"

    filtered = main.businesses(
        q="Cafe",
        country="us",
        region=None,
        city="Oakland",
        category="cafe",
        source_id=source.id,
        limit=10,
        offset=0,
        _="admin",
        db=db,
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["name"] == "Actual Collected Cafe"
    detail = main.business_detail(business.id, _="admin", db=db)
    assert detail["provenance"][0]["source_url"] == "https://example.org/cafe"
    assert detail["provenance"][0]["raw_values"] == {"name": "Actual Collected Cafe"}
    db.close()


def test_source_management_exposes_safe_config_and_validates_enablement():
    factory = database()
    db = factory()
    source = Source(
        code="dataset",
        name="Local dataset",
        source_type="open_dataset",
        connector="dataset",
        config={"path": "/private/local/businesses.json"},
        enabled=True,
        review_status="approved",
    )
    db.add(source)
    db.commit()

    listed = main.sources(_="admin", db=db)[0]
    assert listed["supported_configuration"]["mode"] == "administrator-managed local CSV/JSON file"
    assert "/private/local" not in str(listed)
    assert (
        main.update_source(source.id, SourceUpdate(enabled=False), _="admin", db=db)["enabled"]
        is False
    )
    with pytest.raises(HTTPException) as error:
        main.update_source(source.id, SourceUpdate(enabled=True), _="admin", db=db)
    assert error.value.status_code == 422
    db.close()


def test_schedule_dashboard_actions_use_existing_persisted_schedule_api():
    factory = database()
    db = factory()
    source = Source(
        code="dataset",
        name="Local dataset",
        source_type="open_dataset",
        connector="dataset",
        config={"path": "app/fixtures/synthetic_businesses.json"},
        enabled=True,
        review_status="approved",
        countries=["IN"],
    )
    db.add(source)
    db.commit()
    payload = ScheduleRequest(
        query="cafe",
        countries=["IN"],
        category="cafe",
        source_ids=[source.id],
        interval_minutes=1440,
        max_results=20,
        source_limits={source.id: 10},
    )

    created = main.create_schedule(payload, _="admin", db=db)
    schedule = main.schedules(_="admin", db=db)[0]
    assert created["enabled"] is True
    assert schedule["countries"] == ["IN"]
    assert schedule["max_results"] == 20
    assert schedule["source_limits"] == {source.id: 10}
    assert main.update_schedule(created["id"], enabled=False, _="admin", db=db)["enabled"] is False
    assert main.schedules(_="admin", db=db)[0]["enabled"] is False
    db.close()
