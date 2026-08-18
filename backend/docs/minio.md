# MinIO (Object Storage)

MinIO is an S3-compatible object storage service used to store uploaded invoice files (PDFs, images). It runs locally via Docker and can be swapped for AWS S3 in production with no code changes.

## Running MinIO

MinIO is started as part of the Docker Compose stack:

```bash
cd backend
docker compose up -d minio
```

Or start all services at once:

```bash
docker compose up -d
```

## Access

| Interface   | URL                    |
|-------------|------------------------|
| S3 API      | http://localhost:9000  |
| Web Console | http://localhost:9001  |

**Default credentials:**
- **Username:** `minioadmin`
- **Password:** `minioadmin`

## Web Console

Open http://localhost:9001 in your browser to access the MinIO web console. From here you can:

- Browse and manage buckets
- Upload, download, and delete files
- View storage metrics
- Manage access policies

## Configuration

MinIO connection is configured via environment variables in `backend/.env`:

| Variable             | Default                 | Description              |
|----------------------|-------------------------|--------------------------|
| `FEOH_S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO/S3 API endpoint    |
| `FEOH_S3_ACCESS_KEY`   | `minioadmin`            | Access key (username)    |
| `FEOH_S3_SECRET_KEY`   | `minioadmin`            | Secret key (password)    |
| `FEOH_S3_BUCKET`       | `invoices`              | Bucket name for uploads  |

These are loaded in `backend/app/config.py` via pydantic-settings.

## How It's Used

1. **Invoice upload** — when a user uploads an invoice file (PDF/image), the backend stores it in the MinIO `invoices` bucket using `boto3`
2. **File reference** — the S3 object key and URL are saved on the `invoices` table (`file_key`, `file_url` columns)
3. **File retrieval** — when viewing an invoice, the backend generates a presigned URL or serves the file from MinIO

## Go through `services/storage` — boto3 is blocking

Application code never calls boto3 for object storage directly. Every read,
write and delete goes through `backend/app/services/storage.py`, whose three
primitives — `_put_object` / `_get_object` / `_delete_object` — hand the
blocking boto3 call to `asyncio.to_thread`.

That is not stylistic. boto3 is synchronous and every file surface in this app
(invoice upload, contract / expense / tax-form / chat attachments, the Positive
Pay export, the inbound email and PEPPOL webhooks, the extraction fetch, every
download proxy) is reached from an `async def`. A bare `put_object` there
charges a full S3 round trip — up to `MAX_FILE_SIZE` of body — to the event
loop, and for that whole window the worker answers no other request.

Consequences for callers:

- `storage.get_file(...)` and `storage.delete_file(...)` are **coroutines** —
  `await` them. The `upload_*` helpers already were.
- Need a shape the helpers don't cover (raw bytes, a system-generated file)?
  Call `_put_object` / `_get_object`, not a fresh boto3 client.
- `tests/test_storage_nonblocking.py` is the drift guard: it asserts the calls
  leave the event-loop thread, and AST-scans `app/` for any module issuing a raw
  `put_object` / `get_object` / `delete_object` outside the chokepoint.

## Using boto3

`services/storage` interacts with MinIO using the standard AWS `boto3` SDK.
Example of what it does under the hood (call the module, not this directly):

```python
import boto3
from app.config import settings

s3 = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint_url,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
)

# Upload a file
s3.upload_fileobj(file, settings.s3_bucket, object_key)

# Generate a presigned download URL
url = s3.generate_presigned_url(
    "get_object",
    Params={"Bucket": settings.s3_bucket, "Key": object_key},
    ExpiresIn=3600,
)
```

## Creating a Bucket

The `invoices` bucket needs to exist before uploading files. You can create it via:

**Web Console:** Go to http://localhost:9001 → Buckets → Create Bucket → name it `invoices`

**CLI (mc):**

```bash
# Install MinIO client
brew install minio/stable/mc

# Configure alias
mc alias set local http://localhost:9000 minioadmin minioadmin

# Create bucket
mc mb local/invoices

# List buckets
mc ls local/
```

**Python (boto3):**

```python
s3.create_bucket(Bucket="invoices")
```

## Production

In production, replace MinIO with AWS S3 by changing the environment variables:

```env
FEOH_S3_ENDPOINT_URL=https://s3.amazonaws.com
FEOH_S3_ACCESS_KEY=<your-aws-access-key>
FEOH_S3_SECRET_KEY=<your-aws-secret-key>
FEOH_S3_BUCKET=your-production-bucket
```

No code changes are needed — `boto3` works identically with both MinIO and S3.

## Data Persistence

MinIO data is persisted in a Docker volume (`miniodata`). Removing the container does not delete your files. To fully reset:

```bash
docker compose down -v   # removes volumes too
docker compose up -d     # fresh start
```
