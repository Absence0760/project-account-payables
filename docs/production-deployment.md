# Production Deployment

## Overview

This document describes the recommended AWS deployment architecture for production. The core principle is to run the FastAPI API as a long-lived container and offload async, bursty workloads to Lambda functions.

```
                         CloudFront
                            │
                  ┌─────────┴──────────┐
                  │                    │
            S3 (Frontend)      ALB (API)
                               │
                          ECS / Fargate
                          (FastAPI API)
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         RDS Postgres     ElastiCache       S3 (Files)
         (multi-tenant)    (Redis 7)
                               │
                          SQS Queues
                    ┌──────┬───┴───┬──────┐
                    │      │       │      │
                 Extract  ERP   Audit   (future)
                 Lambda  Lambda Lambda
```

## Service Deployment Targets

| Service | Deploy As | Trigger | Why |
|---|---|---|---|
| FastAPI API | ECS/Fargate | ALB | Connection pooling, low latency, multi-tenant DB routing |
| Frontend | S3 + CloudFront | HTTP | Static SPA, global CDN caching |
| Invoice Extraction | Lambda | SQS | Bursty, CPU-heavy, scales to zero |
| ERP Integration | Lambda | SQS | Async retries, external API isolation, DLQ support |
| Audit Logging | Lambda | EventBridge/SNS | Fire-and-forget, decoupled from request path |

## Container: FastAPI API (ECS/Fargate)

The main API should run as a container behind an Application Load Balancer.

**Why not Lambda:**
- Maintains persistent connection pools to multiple tenant databases
- Multi-tenant session routing (`X-Tenant-Slug` header) benefits from warm, long-lived processes
- Synchronous CRUD endpoints need predictable low latency (no cold starts)
- Simpler to manage CORS, middleware, and dependency injection in a long-running process

**Recommended configuration:**
- ECS service with Fargate launch type
- Minimum 2 tasks across 2 AZs for availability
- ALB health check on `GET /api/health`
- Auto-scaling based on CPU/request count
- Uses the existing `Dockerfile` (`python:3.12-slim` + `uvicorn`)

## Lambda: Invoice Extraction

Already implemented in `backend/app/services/extraction_lambda.py`.

**Handler:** `app.services.extraction_lambda.handler`

**How it works:**
1. API receives invoice upload and writes the file to S3
2. When `extraction_mode = "lambda"`, `extraction_dispatch.py` sends a message to SQS
3. Lambda picks up the message, runs extraction, and writes results back to the tenant DB
4. Configurable via `extraction_mode` and `sqs_extraction_queue_url` in `app/config.py`

**Recommended configuration:**
- Runtime: Python 3.12
- Memory: 512 MB+ (extraction is CPU-bound)
- Timeout: 120s
- Concurrency: reserve based on expected upload volume
- DLQ for failed extractions
- SQS trigger with batch size of 1

## Lambda: ERP Integration (to be built)

Extract the ERP send logic from `backend/app/services/erp.py` into a Lambda handler.

**Why Lambda:**
- ERP calls are async and already fire-and-forget from the user's perspective
- External API calls can be slow/unreliable — Lambda isolates this from the API
- SQS + DLQ gives automatic retry with exponential backoff
- Failed sends land in a DLQ for inspection instead of silently dropping

**Suggested approach:**
- Create `backend/app/services/erp_lambda.py` following the extraction Lambda pattern
- Trigger via SQS queue
- Use the existing `send_to_erp` logic from `erp.py`
- Add a dispatch function in workflow similar to `extraction_dispatch.py`

## Lambda: Audit Logging (to be built)

Extract audit log writes from `backend/app/services/audit.py` into an async Lambda.

**Why Lambda:**
- Audit writes should never block or slow the request path
- EventBridge/SNS trigger allows multiple consumers (logging, analytics, compliance)
- Scales independently from the API

**Suggested approach:**
- Create `backend/app/services/audit_lambda.py`
- Publish audit events to EventBridge or SNS from the API
- Lambda subscribes and writes to the tenant DB

## Managed Services Mapping

| Dev (Docker Compose) | Production (AWS) |
|---|---|
| PostgreSQL 16 container | RDS PostgreSQL (Multi-AZ) |
| Redis 7 container | ElastiCache Redis |
| MinIO container | S3 |
| localhost | Route 53 + CloudFront + ALB |

## Environment Configuration

The FastAPI app uses `pydantic-settings` (`backend/app/config.py`). Key production settings:

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@rds-host:5432/account_payables
TENANT_DB_PREFIX=ap_

# S3 (use real S3, not MinIO)
S3_BUCKET=your-invoice-bucket
# Omit S3_ENDPOINT_URL to use real AWS S3

# Extraction
EXTRACTION_MODE=lambda
SQS_EXTRACTION_QUEUE_URL=https://sqs.region.amazonaws.com/account/extraction-queue

# Redis
REDIS_URL=redis://elasticache-host:6379
```

## Network Architecture

- **VPC** with public and private subnets across 2+ AZs
- **ALB** in public subnets, ECS tasks in private subnets
- **RDS and ElastiCache** in private subnets (no public access)
- **Lambda functions** in private subnets with NAT Gateway for outbound access
- **S3** accessed via VPC endpoint (no NAT needed)
- **SQS/EventBridge** accessed via VPC endpoints
