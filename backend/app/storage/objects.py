from __future__ import annotations

from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from backend.app.config.settings import Settings
from backend.app.utils.paths import validate_package_path


class ObjectStoreError(RuntimeError):
    pass


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def list_prefix(self, prefix: str) -> list[str]: ...
    def ping(self) -> None: ...


def object_key(survey_id: object, relative_path: str) -> str:
    validate_package_path(relative_path)
    return f"{survey_id}/{relative_path}"


class MemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._objects[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        try:
            return self._objects[key][0]
        except KeyError as error:
            raise FileNotFoundError(key) from error

    def exists(self, key: str) -> bool:
        return key in self._objects

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    def list_prefix(self, prefix: str) -> list[str]:
        return sorted(key for key in self._objects if key.startswith(prefix))

    def ping(self) -> None:
        return None


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def ping(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchBucket", "NotFound"}:
                try:
                    self._client.create_bucket(Bucket=self.bucket)
                    return
                except ClientError as create_error:
                    raise ObjectStoreError(
                        "S3-compatible bucket is missing and could not be created. "
                        "Check S3_BUCKET and bucket permissions."
                    ) from create_error
            raise ObjectStoreError(
                "S3-compatible object storage is unavailable. Check S3_ENDPOINT_URL, "
                "S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, and S3_BUCKET on the server."
            ) from error

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from error
            raise ObjectStoreError(f"failed to read object {key}") from error
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ObjectStoreError(f"failed to stat object {key}") from error

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = self._client.list_objects_v2(**kwargs)
            for item in response.get("Contents", []):
                keys.append(item["Key"])
            if not response.get("IsTruncated"):
                return keys
            token = response.get("NextContinuationToken")
