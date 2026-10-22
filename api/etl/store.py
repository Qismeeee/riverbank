from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path


class RawStore(ABC):
    @abstractmethod
    def write(self, rel: str, obj: dict | list) -> str: ...

    @abstractmethod
    def read(self, rel: str) -> dict | list: ...

    @property
    @abstractmethod
    def root(self) -> str: ...


class LocalRawStore(RawStore):
    def __init__(self, base: Path, prefix: str):
        self.dir = base / prefix
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, rel: str, obj: dict | list) -> str:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def read(self, rel: str) -> dict | list:
        return json.loads((self.dir / rel).read_text(encoding="utf-8"))

    @property
    def root(self) -> str:
        return str(self.dir)


class S3RawStore(RawStore):
    def __init__(self, bucket: str, prefix: str, region: str):
        import boto3

        self.bucket, self.prefix = bucket, f"raw/{prefix}"
        self.s3 = boto3.client("s3", region_name=region)

    def write(self, rel: str, obj: dict | list) -> str:
        key = f"{self.prefix}/{rel}"
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")
        return f"s3://{self.bucket}/{key}"

    def read(self, rel: str) -> dict | list:
        obj = self.s3.get_object(Bucket=self.bucket, Key=f"{self.prefix}/{rel}")
        return json.loads(obj["Body"].read())

    @property
    def root(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}/"


class MirroredRawStore(RawStore):
    def __init__(self, primary: RawStore, mirror: RawStore):
        self.primary, self.mirror = primary, mirror

    def write(self, rel: str, obj: dict | list) -> str:
        self.primary.write(rel, obj)
        return self.mirror.write(rel, obj)

    def read(self, rel: str) -> dict | list:
        return self.primary.read(rel)

    @property
    def root(self) -> str:
        return self.mirror.root
