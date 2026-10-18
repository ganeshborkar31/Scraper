import csv
import io
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db import models
from app.db.session import Base, SessionLocal, engine
from app.schemas import ScheduleRequest, SearchRequest
from app.services.discovery import source_compatibility
from app.services.merges import MergeError, merge_businesses, reverse_business_merge
from app.tasks.celery_app import run_job


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.app_env.lower() == "production" and (
        settings.admin_password == "change-this-for-local-development"
        or settings.app_secret_key == "development-only-change-me"
        or len(settings.app_secret_key) < 32
    ):
        raise RuntimeError(
            "Production requires a unique admin password and a 32-character secret key"
        )
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.query(models.Source).filter_by(code="synthetic-fixture").first():
            db.add(
                models.Source(
                    code="synthetic-fixture",
                    name="Synthetic sample dataset",
                    source_type="open_dataset",
                    connector="dataset",
                    config={"path": "app/fixtures/synthetic_businesses.json"},
                    enabled=True,
                    review_status="approved",
                    countries=["IN", "US", "GB"],
                    license_info="Synthetic example records only; not real businesses.",
                )
            )
            db.commit()
        if not db.query(models.Source).filter_by(code="openstreetmap-overpass").first():
            db.add(
                models.Source(
                    code="openstreetmap-overpass",
                    name="OpenStreetMap via Overpass API",
                    source_type="open_data",
                    connector="openstreetmap",
                    config={
                        "endpoint": settings.osm_overpass_url,
                        "user_agent": settings.osm_user_agent,
                        "max_results": 100,
                    },
                    enabled=True,
                    review_status="approved",
                    countries=[],
                    docs_url="https://dev.overpass-api.de/overpass-doc/en/",
                    terms_url="https://www.openstreetmap.org/copyright",
                    license_info=(
                        "Open Database License (ODbL); provide OpenStreetMap attribution "
                        "and comply with share-alike obligations for publicly used derived databases."
                    ),
                    attribution="© OpenStreetMap contributors, ODbL 1.0",
                )
            )
            db.commit()
    yield


app = FastAPI(title="Business Data Discovery", version="0.1.0", lifespan=lifespan)
security = HTTPBasic()


def admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    good_user = secrets.compare_digest(
        credentials.username.encode(), settings.admin_username.encode()
    )
    good_password = secrets.compare_digest(
        credentials.password.encode(), settings.admin_password.encode()
    )
    if not (good_user and good_password):
        raise HTTPException(
            status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Basic"}
        )
    return credentials.username


def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(session)):
    db.execute(__import__("sqlalchemy").text("SELECT 1"))
    return {"status": "ready"}


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Depends(admin), db: Session = Depends(session)):
    source_count = db.query(models.Source).count()
    business_count = db.query(models.Business).count()
    jobs = db.query(models.Job).order_by(models.Job.created_at.desc()).limit(10).all()
    body = "".join(
        f"<li><a href='/jobs/{j.id}'>Job {j.id[:8]}</a> — {j.query} ({j.status})</li>" for j in jobs
    )
    return f"""<!doctype html><html><head><title>Business discovery</title><link rel='stylesheet' href='/static/style.css'></head><body>
    <main><h1>Business data discovery</h1><p>Sources: {source_count} · Businesses: {business_count}</p>
    <h2>Submit a collection</h2><form method='post' action='/ui/jobs'><label>Query <input name='query' required maxlength='500'></label>
    <label>Country <input name='country' value='IN' required maxlength='2'></label><label>City <input name='city'></label><button>Queue job</button></form>
    <h2>Recent jobs</h2><ul>{body}</ul><p><a href='/sources'>Inspect sources (API)</a> · <a href='/businesses'>Browse records (API)</a></p></main></body></html>"""


@app.get("/static/style.css")
def style():
    return Response(
        "body{font:16px system-ui;background:#f4f6f8;color:#17212b}main{max-width:850px;margin:3rem auto;background:white;padding:2rem;border-radius:12px}label{display:block;margin:1rem 0}input{padding:.55rem;margin-left:.5rem}button{padding:.65rem 1rem}",
        media_type="text/css",
    )


@app.post("/ui/jobs")
async def submit_form(request: Request, _: str = Depends(admin), db: Session = Depends(session)):
    form = await request.form()
    payload = SearchRequest(
        query=str(form.get("query", "")),
        country=str(form.get("country", "")),
        city=str(form.get("city") or "") or None,
    )
    return await create_job(payload, db)


@app.get("/sources")
def sources(_: str = Depends(admin), db: Session = Depends(session)):
    return [
        {
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "type": s.source_type,
            "connector": s.connector,
            "enabled": s.enabled,
            "review_status": s.review_status,
            "countries": s.countries,
            "license": s.license_info,
            "last_run_at": s.last_run_at,
            "last_error": s.last_error,
        }
        for s in db.query(models.Source).all()
    ]


@app.post("/jobs", status_code=202)
async def create_job(
    payload: SearchRequest, db: Session = Depends(session), _: str = Depends(admin)
):
    configured = db.query(models.Source).all()
    by_id = {source.id: source for source in configured}
    if payload.source_ids:
        unknown = sorted(set(payload.source_ids) - set(by_id))
        if unknown:
            raise HTTPException(
                422, detail={"message": "Unknown source IDs", "source_ids": unknown}
            )
        selected = [by_id[source_id] for source_id in payload.source_ids]
    else:
        selected = configured
    compatible, unsupported = [], []
    for source in selected:
        supported, reason = source_compatibility(source, payload)
        if supported:
            compatible.append(source)
        else:
            unsupported.append({"id": source.id, "code": source.code, "reason": reason})
    if payload.source_ids and unsupported:
        raise HTTPException(
            422,
            detail={
                "message": "Selected sources cannot support this request",
                "sources": unsupported,
            },
        )
    if not compatible:
        raise HTTPException(
            422,
            detail={
                "message": "No configured source supports this request",
                "sources": unsupported,
            },
        )
    if len(compatible) > 20:
        raise HTTPException(
            422,
            detail={
                "message": "More than 20 sources support this request; select up to 20 explicitly"
            },
        )
    job = models.Job(
        query=payload.query,
        country=payload.country,
        region=payload.region,
        city=payload.city,
        category=payload.category,
        source_ids=[source.id for source in compatible],
        bounds=payload.bounds.model_dump() if payload.bounds else None,
        max_results=payload.max_results,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        run_job.delay(job.id)
    except Exception:
        # Queue failure remains observable as failed rather than returning a phantom accepted job.
        job.status, job.error = "failed", "Background queue unavailable"
        db.commit()
        raise HTTPException(status_code=503, detail="Background queue unavailable")
    return {
        "id": job.id,
        "status": job.status,
        "selected_sources": [{"id": source.id, "code": source.code} for source in compatible],
        "unsupported_sources": unsupported,
    }


@app.get("/jobs")
def list_jobs(
    limit: int = 50, offset: int = 0, _: str = Depends(admin), db: Session = Depends(session)
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid pagination")
    return [
        {
            "id": j.id,
            "query": j.query,
            "country": j.country,
            "region": j.region,
            "bounds": j.bounds,
            "status": j.status,
            "created_at": j.created_at,
        }
        for j in db.query(models.Job)
        .order_by(models.Job.created_at.desc())
        .offset(offset)
        .limit(limit)
    ]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: str = Depends(admin), db: Session = Depends(session)):
    job = db.get(models.Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "id": job.id,
        "query": job.query,
        "country": job.country,
        "region": job.region,
        "bounds": job.bounds,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "outcome": job.outcome,
        "error": job.error,
    }


@app.get("/jobs/{job_id}/results")
def job_results(
    job_id: str,
    limit: int = 50,
    offset: int = 0,
    disposition: str = "all",
    _: str = Depends(admin),
    db: Session = Depends(session),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid pagination")
    if disposition not in {"all", "new", "existing"}:
        raise HTTPException(422, "disposition must be all, new, or existing")
    if not db.get(models.Job, job_id):
        raise HTTPException(404, "Job not found")
    records = (
        db.query(models.SourceRecord, models.Business, models.Source)
        .join(models.Business, models.Business.id == models.SourceRecord.business_id)
        .join(models.Source, models.Source.id == models.SourceRecord.source_id)
        .filter(models.SourceRecord.collection_job_id == job_id)
        .order_by(models.SourceRecord.collected_at, models.SourceRecord.id)
        .all()
    )
    grouped = {}
    for record, business, source in records:
        row = grouped.setdefault(
            business.id,
            {"business": business, "disposition": "existing", "sources": []},
        )
        if record.business_disposition == "new":
            row["disposition"] = "new"
        if not any(entry["source_id"] == source.id for entry in row["sources"]):
            row["sources"].append(
                {
                    "source_id": source.id,
                    "source_code": source.code,
                    "source_url": record.source_url,
                }
            )
    rows = [
        row for row in grouped.values() if disposition == "all" or row["disposition"] == disposition
    ]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "job_id": job_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": row["business"].id,
                "name": row["business"].name,
                "country": row["business"].country,
                "region": row["business"].region,
                "city": row["business"].city,
                "categories": row["business"].categories,
                "disposition": row["disposition"],
                "sources": row["sources"],
            }
            for row in page
        ],
    }


@app.get("/businesses")
def businesses(
    q: str | None = None,
    country: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _: str = Depends(admin),
    db: Session = Depends(session),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid pagination")
    query = db.query(models.Business)
    if q:
        query = query.filter(
            or_(models.Business.name.ilike(f"%{q}%"), models.Business.city.ilike(f"%{q}%"))
        )
    if country:
        query = query.filter(models.Business.country == country.upper())
    total = query.count()
    rows = query.order_by(models.Business.name).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": b.id,
                "name": b.name,
                "domain": b.domain,
                "country": b.country,
                "city": b.city,
                "categories": b.categories,
                "synthetic": b.synthetic,
            }
            for b in rows
        ],
    }


@app.get("/businesses/{business_id}")
def business_detail(business_id: str, _: str = Depends(admin), db: Session = Depends(session)):
    item = db.get(models.Business, business_id)
    if not item:
        raise HTTPException(404, "Business not found")
    sources = (
        db.query(models.SourceRecord, models.Source, models.MatchDecision)
        .join(models.Source, models.Source.id == models.SourceRecord.source_id)
        .outerjoin(
            models.MatchDecision,
            models.MatchDecision.source_record_id == models.SourceRecord.id,
        )
        .filter(models.SourceRecord.business_id == business_id)
        .all()
    )
    return {
        "id": item.id,
        "name": item.name,
        "country": item.country,
        "city": item.city,
        "region": item.region,
        "address": item.address,
        "domain": item.domain,
        "categories": item.categories,
        "email": item.email,
        "phone": item.phone,
        "description": item.description,
        "synthetic": item.synthetic,
        "provenance": [
            {
                "source": s.name,
                "source_code": s.code,
                "source_url": r.source_url,
                "collected_at": r.collected_at,
                "attribution": s.attribution,
                "raw_values": r.raw_values_json,
                "match_classification": decision.classification if decision else None,
                "match_rule": decision.rule if decision else None,
            }
            for r, s, decision in sources
        ],
    }


@app.post("/businesses/{source_business_id}/merge/{target_business_id}")
def merge_business_endpoint(
    source_business_id: str,
    target_business_id: str,
    actor: str = Depends(admin),
    db: Session = Depends(session),
):
    try:
        event = merge_businesses(db, source_business_id, target_business_id, actor)
        db.commit()
    except MergeError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {
        "merge_id": event.id,
        "source_business_id": source_business_id,
        "target_business_id": target_business_id,
    }


@app.post("/merges/{merge_id}/reverse")
def reverse_business_merge_endpoint(
    merge_id: str,
    actor: str = Depends(admin),
    db: Session = Depends(session),
):
    try:
        business = reverse_business_merge(db, merge_id, actor)
        db.commit()
    except MergeError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"merge_id": merge_id, "restored_business_id": business.id}


@app.get("/businesses.csv")
def export_csv(_: str = Depends(admin), db: Session = Depends(session)):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["name", "country", "city", "website", "categories", "synthetic"])
    for item in db.query(models.Business).order_by(models.Business.name):
        values = [
            item.name,
            item.country,
            item.city or "",
            item.domain or "",
            ", ".join(item.categories),
            str(item.synthetic),
        ]
        writer.writerow(
            [
                "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
                for value in values
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=businesses.csv"},
    )


@app.post("/schedules", status_code=201)
def create_schedule(
    payload: ScheduleRequest,
    _: str = Depends(admin),
    db: Session = Depends(session),
):
    from datetime import timedelta

    sources = db.query(models.Source).all()
    by_id = {source.id: source for source in sources}
    if payload.source_ids:
        unknown = sorted(set(payload.source_ids) - set(by_id))
        if unknown:
            raise HTTPException(
                422, detail={"message": "Unknown source IDs", "source_ids": unknown}
            )
        selected = [by_id[source_id] for source_id in payload.source_ids]
    else:
        selected = sources
    unknown_limit_ids = sorted(set(payload.source_limits) - set(by_id))
    if unknown_limit_ids:
        raise HTTPException(
            422, detail={"message": "Unknown source limit IDs", "source_ids": unknown_limit_ids}
        )
    eligible = set()
    for country in payload.countries:
        request = SearchRequest(
            query=payload.query,
            country=country,
            region=payload.region,
            city=payload.city,
            category=payload.category,
            source_ids=payload.source_ids,
            max_results=payload.max_results,
            bounds=payload.bounds,
        )
        compatible = []
        unsupported = []
        for source in selected:
            supported, reason = source_compatibility(source, request)
            if supported:
                compatible.append(source)
            else:
                unsupported.append({"id": source.id, "code": source.code, "reason": reason})
        if payload.source_ids and unsupported:
            raise HTTPException(
                422,
                detail={
                    "message": f"Selected sources cannot support country {country}",
                    "sources": unsupported,
                },
            )
        if not compatible:
            raise HTTPException(
                422,
                detail={
                    "message": f"No configured source supports country {country}",
                    "sources": unsupported,
                },
            )
        eligible.update(source.id for source in compatible)
    if set(payload.source_limits) - eligible:
        raise HTTPException(422, "source_limits may only be set for eligible sources")
    too_frequent = []
    for source in selected:
        if source.id not in eligible:
            continue
        try:
            minimum_interval = max(5, int(source.config.get("min_schedule_interval_minutes", 5)))
        except (TypeError, ValueError):
            minimum_interval = 5
        if payload.interval_minutes < minimum_interval:
            too_frequent.append(source.code)
    if too_frequent:
        raise HTTPException(
            422,
            detail={
                "message": "Frequency violates source-specific schedule limits",
                "sources": too_frequent,
            },
        )
    schedule = models.Schedule(
        query=payload.query,
        country=payload.countries[0],
        countries=payload.countries,
        region=payload.region,
        city=payload.city,
        category=payload.category,
        source_ids=payload.source_ids or [],
        bounds=payload.bounds.model_dump() if payload.bounds else None,
        max_results=payload.max_results,
        source_limits=payload.source_limits,
        interval_minutes=payload.interval_minutes,
        allow_overlap=payload.allow_overlap,
        retry_attempts=payload.retry_attempts,
        enabled=payload.enabled,
        next_run_at=(
            models.now() + timedelta(minutes=payload.interval_minutes) if payload.enabled else None
        ),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return {
        "id": schedule.id,
        "enabled": schedule.enabled,
        "next_run_at": schedule.next_run_at,
        "countries": schedule.countries,
    }


@app.get("/schedules")
def schedules(_: str = Depends(admin), db: Session = Depends(session)):
    return [
        {
            "id": s.id,
            "query": s.query,
            "country": s.country,
            "countries": s.countries or [s.country],
            "region": s.region,
            "city": s.city,
            "category": s.category,
            "bounds": s.bounds,
            "max_results": s.max_results,
            "source_ids": s.source_ids,
            "source_limits": s.source_limits,
            "allow_overlap": s.allow_overlap,
            "retry_attempts": s.retry_attempts,
            "enabled": s.enabled,
            "archived": s.archived,
            "interval_minutes": s.interval_minutes,
            "next_run_at": s.next_run_at,
            "last_run_at": s.last_run_at,
            "last_error": s.last_error,
        }
        for s in db.query(models.Schedule).all()
    ]


@app.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str, enabled: bool, _: str = Depends(admin), db: Session = Depends(session)
):
    from datetime import timedelta

    schedule = db.get(models.Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    if schedule.archived:
        raise HTTPException(409, "Archived schedule cannot be resumed")
    schedule.enabled = enabled
    if enabled and schedule.next_run_at is None:
        schedule.next_run_at = models.now() + timedelta(minutes=schedule.interval_minutes)
    if not enabled:
        schedule.next_run_at = None
    db.commit()
    return {"id": schedule.id, "enabled": schedule.enabled}


@app.get("/schedules/{schedule_id}/runs")
def schedule_runs(
    schedule_id: str,
    limit: int = 50,
    offset: int = 0,
    _: str = Depends(admin),
    db: Session = Depends(session),
):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "invalid pagination")
    if not db.get(models.Schedule, schedule_id):
        raise HTTPException(404, "Schedule not found")
    query = db.query(models.ScheduleExecution).filter_by(schedule_id=schedule_id)
    total = query.count()
    runs = (
        query.order_by(models.ScheduleExecution.scheduled_for.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": run.id,
                "scheduled_for": run.scheduled_for,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "status": run.status,
                "job_ids": run.job_ids,
                "error": run.error,
            }
            for run in runs
        ],
    }


@app.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, _: str = Depends(admin), db: Session = Depends(session)):
    schedule = db.get(models.Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    # Preserve execution and collection-job foreign-key history by archiving.
    schedule.enabled = False
    schedule.archived = True
    schedule.next_run_at = None
    db.commit()
