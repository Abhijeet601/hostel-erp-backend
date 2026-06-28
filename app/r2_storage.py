"""Cloudflare R2 object storage service.

Uses boto3 with S3-compatible endpoint to interact with Cloudflare R2.
Falls back gracefully when R2 credentials are not configured.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from io import BytesIO
from typing import BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import get_settings

logger = logging.getLogger(__name__)


class R2StorageService:
    """Manages file uploads and downloads to Cloudflare R2."""

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.r2_enabled
        self._bucket = settings.r2_bucket_name
        self._public_base_url = settings.r2_public_base_url

        if self._enabled:
            self._client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                region_name="auto",
                config=BotoConfig(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
            logger.info("R2 storage enabled — bucket=%s", self._bucket)
        else:
            self._client = None
            logger.warning("R2 storage disabled — missing credentials. Files stored locally.")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def upload_file(
        self,
        data: bytes | BinaryIO,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload file bytes to R2 and return the public URL or key."""
        if not self._enabled or not self._client:
            raise RuntimeError("R2 storage is not configured.")

        body = data if isinstance(data, bytes) else data.read()
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        logger.info("Uploaded to R2: %s (%d bytes)", key, len(body))
        return self.public_url(key)

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Convenience wrapper for uploading raw bytes."""
        return self.upload_file(data, key, content_type)

    def download_file(self, key: str) -> bytes:
        """Download a file from R2 and return its bytes."""
        if not self._enabled or not self._client:
            raise RuntimeError("R2 storage is not configured.")

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(f"File not found in R2: {key}") from exc
            raise

    def download_to_stream(self, key: str) -> BytesIO:
        """Download a file from R2 and return as BytesIO stream."""
        data = self.download_file(key)
        stream = BytesIO(data)
        stream.seek(0)
        return stream

    def delete_file(self, key: str) -> None:
        """Delete a file from R2."""
        if not self._enabled or not self._client:
            raise RuntimeError("R2 storage is not configured.")

        self._client.delete_object(Bucket=self._bucket, Key=key)
        logger.info("Deleted from R2: %s", key)

    def file_exists(self, key: str) -> bool:
        """Check if a file exists in R2."""
        if not self._enabled or not self._client:
            return False

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def generate_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a temporary presigned URL for downloading a file."""
        if not self._enabled or not self._client:
            raise RuntimeError("R2 storage is not configured.")

        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def public_url(self, key: str) -> str:
        """Return the public URL for a file in R2.

        If a public base URL is configured (e.g. custom domain or r2.dev URL),
        use that. Otherwise fall back to a presigned URL.
        """
        if self._public_base_url:
            return f"{self._public_base_url}/{key}"
        if self._enabled:
            return self.generate_presigned_url(key)
        return ""

    def receipt_key(self, receipt_number: str) -> str:
        """Standard R2 key for a receipt PDF."""
        return f"receipts/{receipt_number}.pdf"

    def photo_key(self, student_id: int, filename: str = "") -> str:
        """Standard R2 key for a student photo."""
        ext = filename.rsplit(".", 1)[-1] if "." in (filename or "") else "jpg"
        return f"photos/student_{student_id}.{ext}"

    def document_key(self, category: str, filename: str) -> str:
        """Standard R2 key for general document uploads."""
        return f"documents/{category}/{filename}"


@lru_cache
def get_r2_service() -> R2StorageService:
    """Cached singleton for the R2 storage service."""
    return R2StorageService()
