from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PortalResult(BaseModel):
    ok: bool
    retrieval_timestamp: datetime = Field(default_factory=utc_now)
    portal_url: str | None = None
    semester: str | None = None
    data: Any = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class TableData(BaseModel):
    title: str | None = None
    headers: list[str]
    rows: list[dict[str, str]]


class LinkData(BaseModel):
    text: str
    url: str
    frame_url: str | None = None
