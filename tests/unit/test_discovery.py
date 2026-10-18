import asyncio
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.db.models import Base, Business, Job, Source, SourceRecord
from app.schemas import SearchRequest
from app.services.discovery import source_compatibility


def test_search_parameters_are_normalized_and_validated():
    request = SearchRequest(query=" bakery ", country="us", region="CA", city="Oakland")
    assert request.query == "bakery"
    assert request.country == "US"
    assert request.region == "CA"
    with pytest.raises(ValueError):
        SearchRequest(query=" ", country="US")
    with pytest.raises(ValueError):
        SearchRequest(query="x", country="USA")


def test_source_compatibility_explains_unsupported_filters():
    source = Source(
        code="local",
        name="Local",
        source_type="test",
        connector="dataset",
        enabled=True,
        review_status="approved",
        countries=["US"],
        config={},
    )
    source.config["path"] = str(
        Path(__file__).parents[2] / "app" / "fixtures" / "synthetic_businesses.json"
    )
    request = SearchRequest(
        query="bakery", country="US", bounds={"south": 1, "west": 1, "north": 1.01, "east": 1.01}
    )
    compatible, reason = source_compatibility(source, request)
    assert not compatible
    assert "bounds" in reason


def test_create_job_selects_compatible_sources_and_results_are_paginated(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    dataset = Source(
        code="local-fixture",
        name="Local fixture",
        source_type="test",
        connector="dataset",
        enabled=True,
        review_status="approved",
        countries=["US"],
        config={
            "path": str(
                Path(__file__).parents[2] / "app" / "fixtures" / "synthetic_businesses.json"
            )
        },
    )
    incompatible = Source(
        code="osm",
        name="OSM",
        source_type="test",
        connector="openstreetmap",
        enabled=True,
        review_status="approved",
        config={"endpoint": "invalid"},
    )
    db.add_all([dataset, incompatible])
    db.commit()

    class Queue:
        queued: ClassVar[list[str]] = []

        @classmethod
        def delay(cls, job_id):
            cls.queued.append(job_id)

    monkeypatch.setattr(main, "run_job", Queue)
    payload = SearchRequest(query="bakery", country="US")
    accepted = asyncio.run(main.create_job(payload, db, "admin"))
    assert accepted["status"] == "queued"
    assert [source["code"] for source in accepted["selected_sources"]] == ["local-fixture"]
    assert accepted["unsupported_sources"][0]["code"] == "osm"
    job = db.get(Job, accepted["id"])
    assert job.source_ids == [dataset.id]

    business = Business(name="New Bakery", normalized_name="new bakery", country="US")
    db.add(business)
    db.flush()
    db.add(
        SourceRecord(
            source_id=dataset.id,
            source_identifier=dataset.code,
            source_name=dataset.name,
            business_id=business.id,
            collection_job_id=job.id,
            business_disposition="new",
        )
    )
    existing_business = Business(
        name="Existing Bakery", normalized_name="existing bakery", country="US"
    )
    db.add(existing_business)
    db.flush()
    db.add(
        SourceRecord(
            source_id=dataset.id,
            source_identifier=dataset.code,
            source_name=dataset.name,
            business_id=existing_business.id,
            collection_job_id=job.id,
            business_disposition="existing",
        )
    )
    db.commit()
    page = main.job_results(job.id, limit=1, offset=0, disposition="new", _="admin", db=db)
    assert page["total"] == 1
    assert page["items"][0]["name"] == "New Bakery"
    existing_page = main.job_results(job.id, disposition="existing", _="admin", db=db)
    assert existing_page["total"] == 1
    assert existing_page["items"][0]["name"] == "Existing Bakery"
    with pytest.raises(HTTPException) as error:
        main.job_results(job.id, limit=201, _="admin", db=db)
    assert error.value.status_code == 422
    db.close()
