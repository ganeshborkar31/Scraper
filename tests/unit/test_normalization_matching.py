from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import Base, Business, BusinessMergeEvent, MatchDecision, Source, SourceRecord
from app.schemas import Candidate, normalize_domain, normalize_name
from app.services.ingest import upsert
from app.services.matching import MatchClass, MatchingRules, best_match
from app.services.merges import merge_businesses, reverse_business_merge
from app.services.normalization import normalize_address, normalize_coordinates, normalize_phone


def database() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def source(db: Session, code: str, config: dict | None = None) -> Source:
    item = Source(
        code=code, name=code, source_type="test", connector="fixture", config=config or {}
    )
    db.add(item)
    db.flush()
    return item


def test_normalizers_fold_names_domains_phones_addresses_and_round_coordinates():
    assert normalize_name("  Café & NORTH-Star, Inc. ") == "cafe north star inc"
    assert normalize_domain("HTTPS://WWW.Example.COM.:443/path") == "example.com"
    assert normalize_phone("+1 (415) 555-0100 ext. 23") == "+14155550100x23"
    assert normalize_phone("0044 20 1234 5678") == "+442012345678"
    assert normalize_address("  10, Café Street. ") == "10 cafe street"
    assert normalize_coordinates(37.12345678, -122.12345678) == (37.123457, -122.123457)


def test_equivalent_domains_and_addresses_match_exactly_and_raw_values_are_kept():
    db = database()
    a, b = source(db, "a"), source(db, "b")
    assert (
        upsert(
            db,
            a,
            Candidate(
                name="Café Market",
                country="US",
                website="https://www.market.example/shop",
                address="10 Café Street",
                raw_values={"website": "HTTPS://WWW.MARKET.EXAMPLE/shop"},
            ),
        )
        == "created"
    )
    db.commit()
    assert (
        upsert(
            db,
            b,
            Candidate(
                name="Cafe Market", country="US", website="market.example", address="10 Cafe Street"
            ),
        )
        == "updated"
    )
    db.commit()
    assert db.query(Business).count() == 1
    assert (
        db.query(MatchDecision).order_by(MatchDecision.created_at.desc()).first().classification
        == "exact"
    )
    records = db.query(SourceRecord).order_by(SourceRecord.source_identifier).all()
    assert records[0].raw_values_json == {"website": "HTTPS://WWW.MARKET.EXAMPLE/shop"}


def test_similar_names_at_different_addresses_and_branches_remain_distinct():
    db = database()
    a, b = source(db, "a"), source(db, "b")
    upsert(
        db,
        a,
        Candidate(
            name="North Star Books",
            country="US",
            website="northstar.example",
            address="10 Main Street",
            city="Oakland",
        ),
    )
    db.commit()
    upsert(
        db,
        b,
        Candidate(
            name="North Star Bookshop",
            country="US",
            website="northstar.example",
            address="200 Broadway",
            city="Oakland",
        ),
    )
    upsert(
        db,
        b,
        Candidate(
            name="North Star Books",
            country="US",
            website="northstar.example",
            address="12 Main Street",
            city="Oakland",
        ),
    )
    db.commit()
    assert db.query(Business).count() == 3
    decisions = [decision.classification for decision in db.query(MatchDecision).all()]
    assert decisions.count("distinct") == 3


def test_missing_contacts_are_safe_and_exact_source_payload_is_idempotent():
    db = database()
    provider = source(db, "no-contact")
    item = Candidate(
        name="Corner Shop",
        country="GB",
        city="Bristol",
        raw_values={"name": "Corner Shop", "country": "gb", "city": "Bristol"},
    )
    assert upsert(db, provider, item) == "created"
    db.commit()
    assert upsert(db, provider, item) == "duplicate"
    db.commit()
    assert db.query(Business).count() == 1
    assert db.query(SourceRecord).count() == 2
    assert db.query(Business).one().normalized_phone is None
    assert db.query(SourceRecord).first().raw_values_json["country"] == "gb"


def test_conflicting_lower_quality_source_does_not_replace_better_canonical_data():
    db = database()
    trusted = source(db, "trusted", {"field_quality": {"name": 90, "website": 90, "phone": 90}})
    weak = source(db, "weak", {"field_quality": {"name": 10, "website": 10, "phone": 10}})
    upsert(
        db,
        trusted,
        Candidate(
            name="Acme Market",
            country="US",
            external_id="trusted-1",
            website="https://acme.example",
            address="1 Market Street",
            phone="+1 415 555 0100",
        ),
    )
    db.commit()
    upsert(
        db,
        weak,
        Candidate(
            name="Acme Market",
            country="US",
            external_id="weak-9",
            website="https://wrong.example",
            address="1 Market Street",
            phone="555-0000",
        ),
    )
    db.commit()
    business = db.query(Business).one()
    assert business.name == "Acme Market"
    assert business.website == "https://acme.example"
    assert business.phone == "+1 415 555 0100"
    assert db.query(SourceRecord).count() == 2


def test_likely_matches_are_recorded_without_automatic_merge_and_thresholds_are_configurable():
    db = database()
    a, b = source(db, "a"), source(db, "b", {"matching_rules": {"likely_threshold": 0.9}})
    upsert(db, a, Candidate(name="Corner Market", country="US", city="Salem"))
    db.commit()
    upsert(db, b, Candidate(name="Corner Market", country="US", city="Salem"))
    db.commit()
    assert db.query(Business).count() == 2
    decisions = db.query(MatchDecision).order_by(MatchDecision.created_at).all()
    assert decisions[1].classification == "likely"
    assert decisions[1].auto_merged is False
    assert decisions[1].candidate_business_id == decisions[0].business_id
    assert (
        MatchingRules.from_config(
            {"likely_threshold": 0.7, "uncertain_threshold": 0.4}
        ).likely_threshold
        == 0.7
    )


def test_name_only_similarity_is_uncertain_and_not_merged():
    db = database()
    a, b = source(db, "a"), source(db, "b")
    upsert(db, a, Candidate(name="Harbor Books", country="US"))
    db.commit()
    upsert(db, b, Candidate(name="Harbor Books", country="US"))
    db.commit()
    decisions = db.query(MatchDecision).order_by(MatchDecision.created_at).all()
    assert decisions[-1].classification == "uncertain"
    assert decisions[-1].auto_merged is False
    assert db.query(Business).count() == 2


def test_manual_merge_is_audited_and_reversible():
    db = database()
    provider = source(db, "a")
    upsert(
        db,
        provider,
        Candidate(name="Shop One", country="US", external_id="1", address="1 Main Street"),
    )
    upsert(db, provider, Candidate(name="Shop Two", country="US", external_id="2"))
    db.commit()
    source_business, target_business = db.query(Business).order_by(Business.name).all()
    source_id, target_id = source_business.id, target_business.id
    event = merge_businesses(db, source_id, target_id, actor="admin")
    db.commit()
    assert db.query(Business).count() == 1
    assert db.get(Business, target_id).address == "1 Main Street"
    assert db.query(SourceRecord).filter_by(business_id=target_id).count() == 2
    assert db.get(BusinessMergeEvent, event.id).actor == "admin"
    restored = reverse_business_merge(db, event.id, actor="reviewer")
    db.commit()
    assert restored.id == source_id
    assert db.query(Business).count() == 2
    assert db.get(Business, target_id).address is None
    assert db.query(SourceRecord).filter_by(business_id=source_id).count() == 1
    assert db.get(BusinessMergeEvent, event.id).reversed_at is not None
    assert db.get(BusinessMergeEvent, event.id).reversed_by == "reviewer"


def test_best_match_no_candidate_is_distinct():
    item = Candidate(name="New", country="US")
    decision = best_match([], item)
    assert decision.classification == MatchClass.DISTINCT
    assert normalize_address(None) is None
