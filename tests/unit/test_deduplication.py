from sqlalchemy import create_engine
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
    assert db.query(SourceRecord).count() == 1


def test_domain_matches_across_sources_and_similar_names_stay_distinct():
    db = database()
    first_source = source(db, "source-a")
    second_source = source(db, "source-b")
    assert (
        upsert(
            db, first_source, Candidate(name="North Star", country="GB", website="north.example")
        )
        == "created"
    )
    db.commit()
    assert (
        upsert(
            db,
            second_source,
            Candidate(name="North Star Ltd", country="GB", website="www.north.example"),
        )
        == "updated"
    )
    assert (
        upsert(db, second_source, Candidate(name="North Star", country="GB", city="Leeds"))
        == "created"
    )
    assert db.query(Business).count() == 2
