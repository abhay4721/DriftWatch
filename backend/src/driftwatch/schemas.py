"""Validated request and response shapes for the public API."""

import uuid
from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class ProjectOut(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class EnvironmentOut(EnvironmentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(pattern="^(opentofu|docker|kubernetes)$")
    path: str = Field(min_length=1, max_length=1024)
    enabled: bool = True
    configuration: dict = Field(default_factory=dict)

    @field_validator("name", "path")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class SourceOut(SourceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    environment_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    address: str
    resource_type: str
    provider_id: str | None
    attributes: dict
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    trigger_type: str
    started_at: datetime | None
    completed_at: datetime | None
    resource_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scan_id: uuid.UUID
    source_id: uuid.UUID
    resource_id: uuid.UUID | None
    fingerprint: str
    resource_address: str
    resource_type: str
    severity: str
    status: str
    recommended_action: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    created_at: datetime


class ResourceChangeOut(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    scan_id: uuid.UUID
    attribute_path: str
    expected_value: object | None
    actual_value: object | None
    sensitive: bool


class FindingDetailOut(FindingOut):
    changes: list[ResourceChangeOut]


class GitHubIssueOut(BaseModel):
    proposal_id: uuid.UUID
    repository: str
    issue_number: int
    issue_url: str
    created: bool


class RemediationProposalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=10, max_length=4000)
    proposed_action: str = Field(min_length=5, max_length=4000)

    @field_validator("title", "summary", "proposed_action")
    @classmethod
    def trim_proposal_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("proposal text cannot be blank")
        return value


class RemediationReview(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note")
    @classmethod
    def trim_review_note(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class RemediationProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_id: uuid.UUID
    status: str
    branch_name: str | None
    pull_request_url: str | None
    proposal: dict
    created_at: datetime
    updated_at: datetime


class PolicyCreate(BaseModel):
    project_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    document: dict
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class PolicyOut(PolicyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    details: dict
    created_at: datetime


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_id: uuid.UUID | None
    channel: str
    status: str
    payload: dict
    sent_at: datetime | None
    created_at: datetime


class NotificationReadOut(BaseModel):
    read_count: int
