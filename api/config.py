import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

ENV = os.getenv("RIVERBANK_ENV", "local")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
]


@lru_cache(maxsize=None)
def _secret(secret_id: str) -> dict:
    import boto3

    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    return json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])


def _from_env_or_secret(env_key: str, secret_id: str, field: str, required: bool = True) -> str:
    value = os.getenv(env_key)
    if value or ENV == "local":
        return value or ""
    try:
        return _secret(secret_id).get(field, "")
    except Exception:
        if required:
            raise
        return ""


OPENAI_API_KEY = _from_env_or_secret("OPENAI_API_KEY", "riverbank/openai", "OPENAI_API_KEY")
ANTHROPIC_API_KEY = _from_env_or_secret(
    "ANTHROPIC_API_KEY", "riverbank/anthropic", "ANTHROPIC_API_KEY"
)
OPENROUTER_API_KEY = _from_env_or_secret(
    "OPENROUTER_API_KEY", "riverbank/openrouter", "OPENROUTER_API_KEY"
)
ADDLIVETAG_API_KEY = _from_env_or_secret(
    "ADDLIVETAG_API_KEY", "riverbank/addlivetag", "ADDLIVETAG_API_KEY", required=False
)


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url or ENV == "local":
        return url or ""
    creds = _secret(os.environ["DB_SECRET_ID"])
    host = os.environ["DB_HOST"]
    db = os.getenv("DB_NAME", "riverbank")
    return f"postgresql://{creds['username']}:{creds['password']}@{host}:5432/{db}"
