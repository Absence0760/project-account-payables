"""Request / response schemas for the assistant API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class UsageDelta(BaseModel):
    input_tokens: int
    output_tokens: int


class ToolInvocationOut(BaseModel):
    tool: str
    args: dict  # PII-safe summary (same shape as the audit row), not raw values
    result: dict | None = None  # full structured tool output (for chart rendering)
    error: str | None = None


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    answer: str
    tool_invocations: list[ToolInvocationOut]
    usage: UsageDelta


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    tool_calls: list[ToolInvocationOut]
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    messages: list[MessageOut]


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]
    total: int


class UsageResponse(BaseModel):
    period: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    budget: int
    remaining: int
    request_count: int
