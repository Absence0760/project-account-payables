"""S3/MinIO file storage service."""

import uuid

import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile

from app.config import settings

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
    )


def _ensure_bucket(client):
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)


async def upload_invoice_file(
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    file: UploadFile,
) -> tuple[str, str]:
    """Upload a file to S3 and return (file_key, file_url)."""
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF")

    file_key = f"{org_id}/{invoice_id}/{file.filename}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )

    # Store an API-relative URL — the file endpoint generates a presigned URL on demand
    file_url = f"/api/invoices/file/{file_key}"
    return file_key, file_url


def get_file(file_key: str) -> tuple[bytes, str]:
    """Download a file from S3 and return (content, content_type)."""
    client = _get_client()
    response = client.get_object(Bucket=settings.s3_bucket, Key=file_key)
    content = response["Body"].read()
    content_type = response.get("ContentType", "application/octet-stream")
    return content, content_type
