import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

ENV = os.getenv("RIVERBANK_ENV", "local")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
S3_BUCKET = os.getenv("S3_BUCKET", "")
WORKFLOW_MODE = os.getenv("RIVERBANK_WORKFLOW_MODE", "disabled")
WORKFLOW_STORAGE = os.getenv("RIVERBANK_WORKFLOW_STORAGE", "memory")
WORKFLOW_FEATURES = os.getenv("RIVERBANK_WORKFLOW_FEATURES", "seller_book")
WORKFLOW_RISK = os.getenv("RIVERBANK_WORKFLOW_RISK", "lightgbm")
WORKFLOW_CIC_GATE = os.getenv("RIVERBANK_WORKFLOW_CIC_GATE", "jev")
WORKFLOW_CIC_SOURCE = os.getenv("RIVERBANK_WORKFLOW_CIC_SOURCE", "profiles")
WORKFLOW_IDENTITY = os.getenv("RIVERBANK_WORKFLOW_IDENTITY", "header")
AUTH_TOKEN_TTL_SECONDS = int(os.getenv("RIVERBANK_AUTH_TOKEN_TTL_SECONDS", "28800"))
AUTH_MAX_FAILURES = int(os.getenv("RIVERBANK_AUTH_MAX_FAILURES", "5"))
AUTH_FAILURE_WINDOW_SECONDS = int(os.getenv("RIVERBANK_AUTH_FAILURE_WINDOW_SECONDS", "300"))
USER_DIRECTORY_PATH = Path(
    os.getenv("RIVERBANK_USER_DIRECTORY") or Path(__file__).resolve().parent / "data" / "users.json"
)
SLOW_REQUEST_MS = int(os.getenv("RIVERBANK_SLOW_REQUEST_MS", "5000"))
WORKFLOW_TEST_DATABASE_URL = os.getenv("WORKFLOW_TEST_DATABASE_URL", "")
GUARD_BLOCK_PROBABILITY = float(os.getenv("RIVERBANK_GUARD_BLOCK_PROBABILITY", "0.5"))
KNOWLEDGE_DIR = Path(
    os.getenv("RIVERBANK_KNOWLEDGE_DIR", Path(__file__).resolve().parent.parent / "knowledge")
)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_CACHE_DIR = os.getenv("LLM_CACHE_DIR", "")
LLM_PURPOSE_LIMITS = {
    "copilot_extract": (20.0, 1),
    "copilot_ask": (20.0, 1),
}
RATE_LIMIT_DEFAULTS = {
    "copilot": "20/60",
    "case_create": "30/3600",
    "statement_upload": "20/3600",
}
STATEMENT_OCR = os.getenv("RIVERBANK_STATEMENT_OCR", "off")
STATEMENT_OCR_PROVIDER = os.getenv("STATEMENT_OCR_PROVIDER", LLM_PROVIDER)
STATEMENT_OCR_MODEL = os.getenv("STATEMENT_OCR_MODEL", LLM_MODEL)
STATEMENT_OCR_MAX_PAGES = int(os.getenv("STATEMENT_OCR_MAX_PAGES", "20"))
STATEMENT_OCR_WORKERS = int(os.getenv("STATEMENT_OCR_WORKERS", "8"))
DOCUMENT_STORE = os.getenv("RIVERBANK_DOCUMENT_STORE", "local")
DOCUMENT_DIR = Path(
    os.getenv(
        "RIVERBANK_DOCUMENT_DIR",
        Path(__file__).resolve().parent.parent / "data" / "statements",
    )
)
JOB_WORKER = os.getenv("RIVERBANK_JOB_WORKER", "on" if ENV == "prod" else "off")
JOB_MAX_ATTEMPTS = int(os.getenv("RIVERBANK_JOB_MAX_ATTEMPTS", "3"))
DB_POOL_MAX_SIZE = int(os.getenv("RIVERBANK_DB_POOL_MAX_SIZE", "5"))
DB_POOL_TIMEOUT_SECONDS = float(os.getenv("RIVERBANK_DB_POOL_TIMEOUT_SECONDS", "10"))
JOB_LEASE_SECONDS = int(os.getenv("RIVERBANK_JOB_LEASE_SECONDS", "600"))
STATEMENT_CLASSIFIER = os.getenv("RIVERBANK_STATEMENT_CLASSIFIER", "jev")
STATEMENT_PAYOUT_PATTERN = os.getenv("RIVERBANK_STATEMENT_PAYOUT_PATTERN", "shopee")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MESSAGE_BUS = os.getenv("RIVERBANK_MESSAGE_BUS", "memory")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "riverbank-api")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "riverbank")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_MECHANISM = os.getenv("KAFKA_SASL_MECHANISM", "")
EVENT_RELAY = os.getenv("RIVERBANK_EVENT_RELAY", "off")
CASE_EVENTS_TOPIC = os.getenv("RIVERBANK_CASE_EVENTS_TOPIC", "riverbank.case-events")
EVENT_RELAY_BATCH_SIZE = int(os.getenv("RIVERBANK_EVENT_RELAY_BATCH_SIZE", "100"))
EVENT_RELAY_IDLE_SECONDS = float(os.getenv("RIVERBANK_EVENT_RELAY_IDLE_SECONDS", "1"))
EVENT_RELAY_RETRY_SECONDS = float(os.getenv("RIVERBANK_EVENT_RELAY_RETRY_SECONDS", "10"))
EVENT_OUTBOX_RETENTION_HOURS = float(os.getenv("RIVERBANK_EVENT_OUTBOX_RETENTION_HOURS", "336"))
EVENT_OUTBOX_PRUNE_SECONDS = float(os.getenv("RIVERBANK_EVENT_OUTBOX_PRUNE_SECONDS", "3600"))
KV_STORE = os.getenv("RIVERBANK_KV_STORE", "memory")
VALKEY_HOST = os.getenv("VALKEY_HOST", "localhost")
VALKEY_PORT = int(os.getenv("VALKEY_PORT", "6379"))
VALKEY_DB = int(os.getenv("VALKEY_DB", "0"))
VALKEY_SSL = os.getenv("VALKEY_SSL", "false").lower() in {"1", "true", "yes"}
VALKEY_KEY_PREFIX = os.getenv("VALKEY_KEY_PREFIX", "riverbank:")
VALKEY_TIMEOUT_SECONDS = float(os.getenv("VALKEY_TIMEOUT_SECONDS", "5"))
MODEL_GATE = os.getenv("RIVERBANK_MODEL_GATE", "on")
MODEL_GATE_STORE = os.getenv("RIVERBANK_MODEL_GATE_STORE", KV_STORE)
MODEL_GATE_DEFAULTS = {"openai": (8, 120), "openrouter": (8, 120), "jev": (8, 120)}
# OCR chạy song song từng trang, một trang bị MODEL_BUSY là hỏng cả sao kê, nên chờ slot lâu hơn
MODEL_GATE_PURPOSE_WAIT = {"statement_ocr": 60.0}
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
AUTH_SIGNING_KEY = _from_env_or_secret(
    "AUTH_SIGNING_KEY", "riverbank/auth", "AUTH_SIGNING_KEY", required=False
)
ACCESS_PASSCODE = _from_env_or_secret(
    "ACCESS_PASSCODE", "riverbank/auth", "ACCESS_PASSCODE", required=False
) or _from_env_or_secret("DEMO_PASSCODE", "riverbank/auth", "DEMO_PASSCODE", required=False)
ADDLIVETAG_API_KEY = _from_env_or_secret(
    "ADDLIVETAG_API_KEY", "riverbank/addlivetag", "ADDLIVETAG_API_KEY", required=False
)


def _service_login(service: str) -> tuple[str, str]:
    keys = [f"{service.upper()}_{field}" for field in ("USERNAME", "PASSWORD")]
    login = [_from_env_or_secret(k, f"riverbank/{service}", k, required=False) for k in keys]
    return login[0], login[1]


KAFKA_USERNAME, KAFKA_PASSWORD = _service_login("kafka")
VALKEY_USERNAME, VALKEY_PASSWORD = _service_login("valkey")


def database_url(refresh: bool = False) -> str:
    url = os.getenv("DATABASE_URL")
    if url or ENV == "local":
        return url or ""
    if refresh:
        _secret.cache_clear()
    creds = _secret(os.environ["DB_SECRET_ID"])
    user, password = (quote(creds[k], safe="") for k in ("username", "password"))
    host = os.environ["DB_HOST"]
    db = os.getenv("DB_NAME", "riverbank")
    return f"postgresql://{user}:{password}@{host}:5432/{db}"


def llm_limits(purpose: str) -> tuple[float, int]:
    timeout, retries = LLM_PURPOSE_LIMITS.get(purpose, (LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES))
    prefix = f"LLM_{purpose.upper()}"
    return (
        float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", timeout)),
        int(os.getenv(f"{prefix}_MAX_RETRIES", retries)),
    )


def rate_limit(scope: str) -> str:
    return os.getenv(f"RIVERBANK_RATE_LIMIT_{scope.upper()}", RATE_LIMIT_DEFAULTS[scope])


def model_gate_limits(provider: str) -> dict:
    concurrency, per_minute = MODEL_GATE_DEFAULTS.get(provider, (8, 120))
    prefix = f"RIVERBANK_MODEL_GATE_{provider.upper()}"
    return {
        "concurrency": int(os.getenv(f"{prefix}_CONCURRENCY", concurrency)),
        "per_minute": int(os.getenv(f"{prefix}_PER_MINUTE", per_minute)),
        "breaker_failures": int(os.getenv("RIVERBANK_MODEL_GATE_BREAKER_FAILURES", "5")),
        "breaker_window_seconds": int(
            os.getenv("RIVERBANK_MODEL_GATE_BREAKER_WINDOW_SECONDS", "60")
        ),
        "breaker_open_seconds": int(os.getenv("RIVERBANK_MODEL_GATE_BREAKER_OPEN_SECONDS", "30")),
        "wait_seconds": float(os.getenv("RIVERBANK_MODEL_GATE_WAIT_SECONDS", "2")),
        "lease_seconds": int(os.getenv("RIVERBANK_MODEL_GATE_LEASE_SECONDS", "120")),
    }


def model_gate_wait(purpose: str) -> float | None:
    wait = os.getenv(f"RIVERBANK_MODEL_GATE_{purpose.upper()}_WAIT_SECONDS")
    return float(wait) if wait else MODEL_GATE_PURPOSE_WAIT.get(purpose)
