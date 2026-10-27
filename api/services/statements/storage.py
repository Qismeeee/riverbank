import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from services.underwriting.models import WorkflowError

from .documents import StatementDocument

EXTENSIONS = {
    "text/csv": "csv",
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/heic": "heic",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


class DocumentStore(ABC):
    name: str

    @abstractmethod
    def put(self, document: StatementDocument, case_id: str) -> dict: ...

    @abstractmethod
    def get(self, record: dict) -> tuple[bytes, str]: ...


class NoDocumentStore(DocumentStore):
    name = "none"

    def put(self, document: StatementDocument, case_id: str) -> dict:
        return {"store": self.name}

    def get(self, record: dict) -> tuple[bytes, str]:
        raise WorkflowError("STATEMENT_FILE_NOT_STORED", 404)


class S3DocumentStore(DocumentStore):
    name = "s3"

    def __init__(self, bucket: str, region: str, prefix: str = "statements", client=None):
        if not bucket:
            raise WorkflowError("DOCUMENT_STORE_NOT_CONFIGURED", 503)
        self.bucket, self.prefix = bucket, prefix
        self._client = client or self._default_client(region)

    @staticmethod
    def _default_client(region: str):
        import boto3

        return boto3.client("s3", region_name=region)

    def put(self, document: StatementDocument, case_id: str) -> dict:
        from botocore.exceptions import BotoCoreError, ClientError

        digest = hashlib.sha256(document.content).hexdigest()
        key = f"{self.prefix}/{case_id}/{digest}.{EXTENSIONS[document.media_type]}"
        try:
            response = self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=document.content,
                ContentType=document.media_type,
                ServerSideEncryption="AES256",
                Metadata={"case-id": case_id, "sha256": digest},
            )
        except (BotoCoreError, ClientError) as exc:
            raise WorkflowError("DOCUMENT_STORE_UNAVAILABLE", 503, [type(exc).__name__]) from exc
        return {
            "store": self.name,
            "bucket": self.bucket,
            "key": key,
            "version_id": response.get("VersionId"),
        }

    def get(self, record: dict) -> tuple[bytes, str]:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self._client.get_object(Bucket=record["bucket"], Key=record["key"])
            body = response["Body"].read()
        except KeyError as exc:
            raise WorkflowError("STATEMENT_FILE_NOT_STORED", 404) from exc
        except (BotoCoreError, ClientError) as exc:
            raise WorkflowError("DOCUMENT_STORE_UNAVAILABLE", 503, [type(exc).__name__]) from exc
        return body, response.get("ContentType") or "application/octet-stream"


class LocalDocumentStore(DocumentStore):
    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, document: StatementDocument, case_id: str) -> dict:
        digest = hashlib.sha256(document.content).hexdigest()
        relative = f"{case_id}/{digest}.{EXTENSIONS[document.media_type]}"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(document.content)
        return {"store": self.name, "path": relative, "media_type": document.media_type}

    def get(self, record: dict) -> tuple[bytes, str]:
        relative = record.get("path") or ""
        root = self.root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise WorkflowError("STATEMENT_FILE_NOT_STORED", 404)
        media = record.get("media_type") or "application/octet-stream"
        return path.read_bytes(), media
