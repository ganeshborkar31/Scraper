from app.connectors.registry import CONNECTORS
from app.db.models import Business, Source, SourceRecord, now
from app.db.session import SessionLocal
from app.schemas import Candidate, SearchRequest, normalize_domain, normalize_name


def ingest_job(job_id: str) -> None:
    db = SessionLocal()
    job = db.get(__import__("app.db.models", fromlist=["Job"]).Job, job_id)
    if not job:
        db.close()
        return
    job.status, job.started_at = "running", now()
    db.commit()
    per_source: dict[str, dict] = {}
    discovered = created = updated = duplicates = 0
    successes = failures = 0
    request = SearchRequest(
        query=job.query,
        country=job.country,
        city=job.city,
        category=job.category,
        source_ids=job.source_ids,
        max_results=job.max_results,
    )
    sources = (
        db.query(Source).filter(Source.enabled.is_(True), Source.review_status == "approved").all()
    )
    if job.source_ids:
        sources = [source for source in sources if source.id in job.source_ids]
    for source in sources:
        connector = CONNECTORS.get(source.connector)
        try:
            if connector is None:
                raise ValueError("connector is unavailable")
            valid, reason = connector.validate(source.config)
            if not valid:
                raise ValueError(reason or "connector is not configured")
            candidates = connector.search(request, source.config)
            source_created = source_updated = source_duplicates = 0
            for item in candidates:
                discovered += 1
                result = upsert(db, source, item)
                source_created += result == "created"
                source_updated += result == "updated"
                source_duplicates += result == "duplicate"
            created += source_created
            updated += source_updated
            duplicates += source_duplicates
            source.last_success_at = source.last_run_at = now()
            source.last_error = None
            per_source[source.code] = {
                "status": "succeeded",
                "discovered": len(candidates),
                "created": source_created,
                "updated": source_updated,
                "duplicates": source_duplicates,
            }
            successes += 1
        except Exception as exc:
            source.last_run_at, source.last_error = now(), str(exc)[:500]
            per_source[source.code] = {"status": "failed", "error": str(exc)[:300]}
            failures += 1
        db.commit()
    job.outcome = {
        "sources": per_source,
        "discovered": discovered,
        "created": created,
        "updated": updated,
        "duplicates": duplicates,
    }
    job.status = (
        "partial_success" if successes and failures else "succeeded" if successes else "failed"
    )
    job.error = "No approved compatible sources succeeded" if not successes else None
    job.completed_at = now()
    db.commit()
    db.close()


def upsert(db, source: Source, item: Candidate) -> str:
    if item.external_id:
        existing_record = (
            db.query(SourceRecord)
            .filter_by(source_id=source.id, external_id=item.external_id)
            .first()
        )
        if existing_record:
            business = existing_record.business
            business.last_seen = now()
            return "duplicate"
    domain = normalize_domain(item.website)
    business = (
        db.query(Business)
        .filter(Business.domain == domain, Business.country == item.country)
        .first()
        if domain
        else None
    )
    if not business:
        business = (
            db.query(Business)
            .filter_by(
                country=item.country, normalized_name=normalize_name(item.name), city=item.city
            )
            .first()
        )
    if business:
        business.last_seen = now()
        if not business.domain and domain:
            business.domain = domain
        outcome = "updated"
    else:
        business = Business(
            name=item.name,
            normalized_name=normalize_name(item.name),
            domain=domain,
            country=item.country,
            city=item.city,
            region=item.region,
            address=item.address,
            postal_code=item.postal_code,
            latitude=item.latitude,
            longitude=item.longitude,
            categories=item.categories,
            email=item.email,
            phone=item.phone,
            description=item.description,
            synthetic=item.synthetic,
        )
        db.add(business)
        db.flush()
        outcome = "created"
    db.add(
        SourceRecord(
            source_id=source.id,
            business_id=business.id,
            external_id=item.external_id,
            source_url=item.source_url,
            metadata_json={"attribution": source.attribution or ""},
        )
    )
    db.flush()
    return outcome
