import socket
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config

app = FastAPI(title="Riverbank API", version="0.1.0")

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
