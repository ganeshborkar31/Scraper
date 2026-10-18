import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Base, Business, Source, SourceRecord
from app.schemas import Candidate
from app.services.ingest import upsert


def database() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def source(db: Session, code: str) -> Source:
    item = Source(code=code, name=code, source_type="open_dataset", connector="dataset")
    db.add(item)
    db.flush()
    return item


def test_same_external_id_is_idempotent():
    db = database()
    provider = source(db, "source-a")
    candidate = Candidate(name="Acme", country="US", external_id="42")
    assert upsert(db, provider, candidate) == "created"
    db.commit()
    assert upsert(db, provider, candidate) == "duplicate"
    assert db.query(Business).count() == 1
    assert db.query(SourceRecord).count() == 2


def test_repeat_external_id_keeps_every_source_observation():
    db = database()
    provider = source(db, "source-a")
    candidate = Candidate(
        name="Acme", country="US", external_id="42", source_url="https://source.test/42"
    )
    assert upsert(db, provider, candidate) == "created"
    db.commit()
    assert upsert(db, provider, candidate) == "duplicate"
    db.commit()
    records = db.query(SourceRecord).all()
    assert len(records) == 2
    assert {record.source_identifier for record in records} == {"source-a"}
    assert {record.source_name for record in records} == {"source-a"}
    assert all(record.source_url == "https://source.test/42" for record in records)


def test_business_attributes_and_database_coordinate_constraints():
    db = database()
    provider = source(db, "source-a")
    candidate = Candidate(
        name="Acme",
        country="US",
        website="https://www.acme.example",
        latitude=37.5,
        longitude=-122.2,
        categories=["retail"],
        email="hello@acme.example",
    )
    upsert(db, provider, candidate)
    db.commit()
    business = db.query(Business).one()
    assert business.website == candidate.website
    assert business.domain == "acme.example"
    assert business.latitude == 37.5
    assert business.email == "hello@acme.example"
    assert business.created_at and business.updated_at
    db.add(Business(name="Bad", normalized_name="bad", country="US", latitude=91))
    with pytest.raises(IntegrityError):
        db.commit()


def test_business_update_changes_record_and_timestamp():
    db = database()
    provider = source(db, "source-a")
    upsert(db, provider, Candidate(name="Acme", country="US", external_id="42"))
    db.commit()
    business = db.query(Business).one()
    original_update = business.updated_at
    business.name = "Acme Incorporated"
    db.commit()
    assert business.name == "Acme Incorporated"
    assert business.updated_at >= original_update


def test_duplicate_source_entity_can_have_multiple_observations():
    db = database()
    provider = source(db, "source-a")
    upsert(db, provider, Candidate(name="Acme", country="US", external_id="42"))
    db.commit()
    upsert(db, provider, Candidate(name="Acme", country="US", external_id="42"))
    db.commit()
    assert db.query(SourceRecord).filter_by(source_id=provider.id, external_id="42").count() == 2


def test_domain_matches_across_sources_and_similar_names_stay_distinct():
    db = database()
    first_source = source(db, "source-a")
    second_source = source(db, "source-b")
    assert (
        upsert(
            db,
            first_source,
            Candidate(
                name="North Star",
                country="GB",
                website="north.example",
                address="10 High Street",
                city="London",
            ),
        )
        == "created"
    )
    db.commit()
    assert (
        upsert(
            db,
            second_source,
            Candidate(
                name="North Star",
                country="GB",
                website="https://www.north.example/path",
                address="10 High Street",
                city="London",
            ),
        )
        == "updated"
    )
    assert (
        upsert(db, second_source, Candidate(name="North Star", country="GB", city="Leeds"))
        == "created"
    )
    assert db.query(Business).count() == 2
