"""S3/MinIO file storage service."""

import re
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
    # Structured e-invoices (UBL 2.1 / standalone CII) arrive as raw XML;
    # Factur-X / ZUGFeRD arrive as PDF and are covered by application/pdf.
    "application/xml",
    "text/xml",
}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB

# Contract documents are usually signed PDFs but may also arrive as Word
# files, so the contract repository accepts a superset of the invoice types.
CONTRACT_CONTENT_TYPES = ALLOWED_CONTENT_TYPES | {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Strips path separators, parent-directory tokens, and control chars
# from a user-supplied filename before it's interpolated into the S3
# key. Without this, a crafted filename like `../../other-org/x.pdf`
# could land under another tenant's prefix.
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(name: str | None) -> str:
    """Return a filesystem-safe basename. None / empty / all-stripped
    inputs fall back to a synthetic name so a missing client header
    doesn't end up with an empty S3 key segment."""
    base = (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _SAFE_FILENAME.sub("_", base)
    # Strip leading dots so an attacker can't store a dotfile.
    cleaned = cleaned.lstrip(".")
    return cleaned or "upload"


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
        raise ValueError(
            f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF, XML"
        )

    file_key = f"{org_id}/{invoice_id}/{_safe_filename(file.filename)}"

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


async def upload_contract_file(
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    file: UploadFile,
) -> tuple[str, str]:
    """Upload a contract document to S3 and return (file_key, file_url).

    The key is ``<org_id>/contracts/<contract_id>/<safe-filename>`` — the
    leading ``org_id`` segment is the cross-tenant download gate (the
    contracts file endpoint refuses keys whose first segment isn't the
    caller's org), mirroring the invoice file path.
    """
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in CONTRACT_CONTENT_TYPES:
        raise ValueError(
            f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF, XML, Word"
        )

    file_key = f"{org_id}/contracts/{contract_id}/{_safe_filename(file.filename)}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )

    file_url = f"/api/contracts/file/{file_key}"
    return file_key, file_url


async def upload_expense_receipt(
    org_id: uuid.UUID,
    expense_id: uuid.UUID,
    file: UploadFile,
) -> tuple[str, str]:
    """Upload an expense receipt to S3 and return (file_key, file_url).

    The key is ``<org_id>/expenses/<expense_id>/<safe-filename>`` — the
    leading ``org_id`` segment is the cross-tenant download gate (the
    expense receipt endpoint refuses keys whose first segment isn't the
    caller's org), mirroring the invoice / contract file paths. Receipts are
    photographed, so the invoice-grade ``ALLOWED_CONTENT_TYPES`` (PDF / PNG /
    JPEG / TIFF / XML) applies — no Word documents.
    """
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF, XML"
        )

    file_key = f"{org_id}/expenses/{expense_id}/{_safe_filename(file.filename)}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )

    file_url = f"/api/expenses/receipt/{file_key}"
    return file_key, file_url


async def upload_chat_file(
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    message_id: uuid.UUID,
    file: UploadFile,
) -> tuple[str, str, str, int]:
    """Upload a supplier-chat attachment to S3.

    Returns ``(file_key, filename, content_type, size)``. The key is
    ``<org_id>/chat/<invoice_id>/<message_id>/<safe-filename>`` — the leading
    ``org_id`` segment is the cross-tenant download gate (the chat file
    endpoints refuse keys whose first segment isn't the caller's org),
    mirroring the invoice / contract file paths. The ``file_url`` is built by
    the caller (it differs between the AP and portal surfaces).
    """
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF, XML"
        )

    safe_name = _safe_filename(file.filename)
    file_key = f"{org_id}/chat/{invoice_id}/{message_id}/{safe_name}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )
    return file_key, safe_name, content_type, len(content)


def get_file(file_key: str) -> tuple[bytes, str]:
    """Download a file from S3 and return (content, content_type)."""
    client = _get_client()
    response = client.get_object(Bucket=settings.s3_bucket, Key=file_key)
    content = response["Body"].read()
    content_type = response.get("ContentType", "application/octet-stream")
    return content, content_type
