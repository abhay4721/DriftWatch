"""Stable models shared by provider adapters and the drift engine."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class AttributeChange(BaseModel):
    path: str
    expected: Any
    actual: Any
    sensitive: bool = False


class DriftFinding(BaseModel):
    id: str
    resource_address: str
    resource_type: str
    attribute_changes: list[AttributeChange]
    severity: str
    recommended_action: str


class ScannedResource(BaseModel):
    address: str
    resource_type: str


class ScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_id: UUID = Field(default_factory=uuid4)
    provider: str
    source: str
    status: str = "running"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    resource_count: int = 0
    resources: list[ScannedResource] = Field(default_factory=list)
    findings: list[DriftFinding] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
