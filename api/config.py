"""
Cấu hình chạy.

- local  : lấy từ biến môi trường / file .env, không chạm AWS.
- prod   : lấy key từ AWS Secrets Manager qua IAM role của EC2, không có file key nào trên đĩa.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# .env ở root repo, chỉ cho local. Trong container không có file này, bỏ qua.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

ENV = os.getenv("RIVERBANK_ENV", "local")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
]


@lru_cache(maxsize=None)
def _secret(secret_id: str) -> dict:
    """Đọc một secret JSON từ Secrets Manager. Cache vĩnh viễn trong tiến trình."""
    import boto3

    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    return json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])


def _from_env_or_secret(env_key: str, secret_id: str, field: str) -> str:
    value = os.getenv(env_key)
    if value or ENV == "local":
        return value or ""
    return _secret(secret_id).get(field, "")


OPENAI_API_KEY = _from_env_or_secret(
    "OPENAI_API_KEY", "riverbank/openai", "OPENAI_API_KEY")
ANTHROPIC_API_KEY = _from_env_or_secret(
    "ANTHROPIC_API_KEY", "riverbank/anthropic", "ANTHROPIC_API_KEY"
)
OPENROUTER_API_KEY = _from_env_or_secret(
    "OPENROUTER_API_KEY", "riverbank/openrouter", "OPENROUTER_API_KEY"
)


def database_url() -> str:
    """Local: DATABASE_URL trong .env. Prod: ghép từ secret RDS + DB_HOST."""
    url = os.getenv("DATABASE_URL")
    if url or ENV == "local":
        return url or ""
    creds = _secret(os.environ["DB_SECRET_ID"])
    host = os.environ["DB_HOST"]
    db = os.getenv("DB_NAME", "riverbank")
    return f"postgresql://{creds['username']}:{creds['password']}@{host}:5432/{db}"
