"""S3/MinIO file storage service."""

import re
import uuid

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile

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
    # Empty FEOH_S3_ENDPOINT_URL targets real AWS S3; empty access keys defer
    # to boto3's default credential chain (instance profile / env vars) —
    # how deployed environments authenticate. The committed dev defaults
    # (localhost MinIO + minioadmin) keep the local-first behaviour.
    kwargs: dict = {}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key and settings.s3_secret_key:
        kwargs["aws_access_key_id"] = settings.s3_access_key
        kwargs["aws_secret_access_key"] = settings.s3_secret_key
    return boto3.client("s3", **kwargs)


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


async def upload_tax_form_file(
    org_id: uuid.UUID,
    vendor_id: uuid.UUID,
    form_type: str,
    file: UploadFile,
) -> tuple[str, str]:
    """Upload a vendor's signed W-9 / W-8 tax form to S3 and return
    ``(file_key, file_url)``.

    The key is ``<org_id>/tax-forms/<vendor_id>/<form_type>/<safe-filename>`` —
    the leading ``org_id`` segment is the cross-tenant download gate (the tax
    form download endpoint refuses keys whose first segment isn't the caller's
    org), mirroring the invoice / contract / expense file paths. The
    ``form_type`` segment lets the read path recover whether the on-file form is
    a W-9 or W-8 without adding a vendor column (no migration). ``form_type`` is
    validated by the caller against a fixed allowlist, so it can't smuggle a
    path separator into the key.

    Tax forms are signed PDFs (occasionally scanned to image), so the
    invoice-grade ``ALLOWED_CONTENT_TYPES`` (PDF / PNG / JPEG / TIFF / XML)
    applies — no Word documents.
    """
    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"File type '{content_type}' not allowed. Accepted: PDF, PNG, JPEG, TIFF, XML"
        )

    file_key = f"{org_id}/tax-forms/{vendor_id}/{form_type}/{_safe_filename(file.filename)}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type,
    )

    file_url = "/api/portal/company/tax-form/file"
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


async def upload_vendor_statement_file(
    org_id: uuid.UUID,
    reconciliation_id: uuid.UUID,
    content: bytes,
    filename: str,
    content_type: str,
) -> str:
    """Archive the supplier statement a reconciliation run was built from.

    Returns the ``file_key``. The key is
    ``<org_id>/vendor-statements/<reconciliation_id>/<safe-filename>`` — the
    leading ``org_id`` segment is the cross-tenant download gate (the run's
    download endpoint refuses a key whose first segment isn't the caller's
    org), mirroring the invoice / contract / positive-pay paths.

    Why store it at all: a run's per-line ``raw`` JSONB preserves what we
    PARSED, which is enough to replay the match but not to answer "did we read
    the supplier's document correctly?" — the question an auditor or a disputed
    balance actually raises. The original document is the only answer to that,
    and it matters most on the PDF path, where a model did the reading.

    Takes already-read bytes rather than an ``UploadFile`` because the caller
    has parsed them first: nothing is archived until the statement has produced
    a run, so a junk upload never lands in the bucket.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    file_key = f"{org_id}/vendor-statements/{reconciliation_id}/{_safe_filename(filename)}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )
    return file_key


async def upload_positive_pay_file(
    org_id: uuid.UUID,
    file_id: uuid.UUID,
    content: bytes,
    filename: str,
    content_type: str,
) -> tuple[str, str]:
    """Store a system-generated Positive Pay export in S3.

    The key is ``<org_id>/positive-pay/<file_id>/<safe-filename>`` — the
    leading ``org_id`` segment is the cross-tenant download gate (the positive
    pay download endpoint refuses keys whose first segment isn't the caller's
    org), mirroring the invoice / contract / expense file paths.

    Unlike the upload helpers above, the content is already-rendered bytes
    (not an ``UploadFile``) and is system-generated — there's no content-type
    allowlist to enforce — but the size is still capped defensively. The
    rendered file legitimately contains full account / routing numbers (that's
    the file's purpose); it lives only here in MinIO, never in a DB column or a
    log line.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)} MB")

    file_key = f"{org_id}/positive-pay/{file_id}/{_safe_filename(filename)}"

    client = _get_client()
    _ensure_bucket(client)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=file_key,
        Body=content,
        ContentType=content_type or "application/octet-stream",
    )

    file_url = f"/api/positive-pay/{file_id}/download"
    return file_key, file_url


def get_file(file_key: str, *, expected_prefix: str | None = None) -> tuple[bytes, str]:
    """Download a file from S3 and return (content, content_type).

    SECURITY — this is a raw object fetch with NO tenant/org scoping of its own.
    Every stored key is namespaced by a leading `<organization_id>/...` (or
    `<organization_id>/chat/<invoice_id>/...`) segment, but this function does
    not enforce that: whatever key the caller passes is fetched verbatim. The
    CALLER MUST validate the key against the requesting principal's tenant/owner
    before calling — otherwise a user-supplied `file_key` is a cross-tenant file
    IDOR. Every current call site does this (portal chat/tax-form downloads, the
    AP contract/expense/workflow file endpoints each check the leading segment).

    Pass `expected_prefix` to have this function enforce the check itself: the
    key must start with that prefix or a 404 `HTTPException` is raised (the same
    opaque status the ownership checks use, so it never enumerates). Prefer
    passing it wherever the caller knows the owning prefix — belt-and-suspenders
    on top of the caller's own check.
    """
    if expected_prefix is not None and not file_key.startswith(expected_prefix):
        # 404 (not 403) so a probe can't distinguish "wrong owner" from
        # "missing key" — matches the cross-tenant download guards elsewhere.
        raise HTTPException(status_code=404, detail="File not found")
    client = _get_client()
    response = client.get_object(Bucket=settings.s3_bucket, Key=file_key)
    content = response["Body"].read()
    content_type = response.get("ContentType", "application/octet-stream")
    return content, content_type


def delete_file(file_key: str) -> None:
    """Best-effort delete of a stored object.

    Used when the owning DB row is removed and the bytes must not linger at
    rest — notably the Positive Pay export, the one stored file that carries
    full account / routing numbers. Swallows a missing-object / transport error
    so a storage hiccup never blocks the DB delete that calls it; S3/MinIO
    ``delete_object`` is itself idempotent (no error on an absent key).
    """
    if not file_key:
        return
    try:
        client = _get_client()
        client.delete_object(Bucket=settings.s3_bucket, Key=file_key)
    except Exception:
        # The DB row is the source of truth; a bucket-lifecycle / retention
        # sweep is the backstop for any object this best-effort call misses.
        pass
