import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
from routers.auth import router as auth_router
from routers.copilot import cases_router as copilot_cases_router
from routers.copilot import router as copilot_router
from routers.jobs import router as jobs_router
from routers.jobs import start_worker
from routers.studio import router as studio_router
from routers.underwriting import router as underwriting_router
from services.underwriting.models import WorkflowError


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = start_worker()
    yield
    if stop:
        stop.set()


app = FastAPI(title="Riverbank API", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(underwriting_router)
app.include_router(studio_router)
app.include_router(copilot_router)
app.include_router(copilot_cases_router)
app.include_router(jobs_router)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(request, exc: WorkflowError):
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "details": exc.details}},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_db() -> str:
    url = config.database_url()
    if not url:
        return "skipped"
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("select 1")
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}"


def _check_s3() -> str:
    if not config.S3_BUCKET:
        return "skipped"
    try:
        import boto3

        boto3.client("s3", region_name=config.AWS_REGION).head_bucket(Bucket=config.S3_BUCKET)
        return "ok"
    except Exception as e:
        return f"error: {type(e).__name__}"


@app.get("/health")
def health():
    db, s3 = _check_db(), _check_s3()
    ok = all(v in ("ok", "skipped") for v in (db, s3))
    return {
        "status": "ok" if ok else "degraded",
        "env": config.ENV,
        "db": db,
        "s3": s3,
        "host": socket.gethostname(),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
def root():
    return {"service": "riverbank-api", "docs": "/docs"}
