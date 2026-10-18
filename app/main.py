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
from app.schemas import SearchRequest
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
    job = models.Job(
        query=payload.query,
        country=payload.country,
        city=payload.city,
        category=payload.category,
        source_ids=payload.source_ids or [],
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
    return {"id": job.id, "status": job.status}


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
        "bounds": job.bounds,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "outcome": job.outcome,
        "error": job.error,
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
        db.query(models.SourceRecord, models.Source)
        .join(models.Source, models.Source.id == models.SourceRecord.source_id)
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
            }
            for r, s in sources
        ],
    }


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
    query: str,
    country: str,
    interval_minutes: int = 1440,
    source_ids: list[str] | None = None,
    _: str = Depends(admin),
    db: Session = Depends(session),
):
    if not query.strip() or not 5 <= interval_minutes <= 525600 or len(country) != 2:
        raise HTTPException(422, "invalid schedule")
    from datetime import timedelta

    schedule = models.Schedule(
        query=query,
        country=country.upper(),
        source_ids=source_ids or [],
        interval_minutes=interval_minutes,
        next_run_at=models.now() + timedelta(minutes=interval_minutes),
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return {"id": schedule.id, "enabled": schedule.enabled, "next_run_at": schedule.next_run_at}


@app.get("/schedules")
def schedules(_: str = Depends(admin), db: Session = Depends(session)):
    return [
        {
            "id": s.id,
            "query": s.query,
            "country": s.country,
            "enabled": s.enabled,
            "interval_minutes": s.interval_minutes,
            "next_run_at": s.next_run_at,
            "last_run_at": s.last_run_at,
        }
        for s in db.query(models.Schedule).all()
    ]


@app.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str, enabled: bool, _: str = Depends(admin), db: Session = Depends(session)
):
    schedule = db.get(models.Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    schedule.enabled = enabled
    db.commit()
    return {"id": schedule.id, "enabled": schedule.enabled}


@app.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, _: str = Depends(admin), db: Session = Depends(session)):
    schedule = db.get(models.Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    db.delete(schedule)
    db.commit()
