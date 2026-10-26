import hashlib
from abc import ABC, abstractmethod

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


class NoDocumentStore(DocumentStore):
    name = "none"

    def put(self, document: StatementDocument, case_id: str) -> dict:
        return {"store": self.name}


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
