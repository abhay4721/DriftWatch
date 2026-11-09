"""Versioned REST API for project configuration and scan history."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from driftwatch.config import settings
from driftwatch.database import get_db
from driftwatch.db_models import (
    AuditLog,
    DriftFinding,
    Environment,
    InfrastructureSource,
    Policy,
    Project,
    RemediationProposal,
    Resource,
    ResourceChange,
    Scan,
)
from driftwatch.github import GitHubIntegrationError, create_issue
from driftwatch.scan_service import enqueue_scan
from driftwatch.schemas import (
    AuditLogOut,
    EnvironmentCreate,
    EnvironmentOut,
    FindingDetailOut,
    FindingOut,
    GitHubIssueOut,
    Page,
    PolicyCreate,
    PolicyOut,
    ProjectCreate,
    ProjectOut,
    ResourceChangeOut,
    ResourceOut,
    ScanOut,
    SourceCreate,
    SourceOut,
)

router = APIRouter(prefix="/api/v1")
MAX_PAGE_SIZE = 100


def _page_args(
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE), offset: int = Query(default=0, ge=0)
) -> tuple[int, int]:
    return limit, offset


def _get_or_404(db: Session, model: type, identifier: uuid.UUID, label: str):
    value = db.get(model, identifier)
    if value is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": f"{label} not found"}
        )
    return value


def _record_audit(
    db: Session, action: str, entity_type: str, entity_id: uuid.UUID, details: dict
) -> None:
    db.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
        )
    )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "conflict",
                "message": "A record with these unique values already exists",
            },
        ) from exc


def _paged(db: Session, statement, count_statement, limit: int, offset: int, schema):
    items = db.scalars(statement.limit(limit).offset(offset)).all()
    total = db.scalar(count_statement) or 0
    return Page[schema](items=items, total=total, limit=limit, offset=offset)


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    project = Project(id=uuid.uuid4(), name=payload.name.strip(), description=payload.description)
    db.add(project)
    _record_audit(db, "project.created", "project", project.id, {"name": project.name})
    _commit(db)
    db.refresh(project)
    return project


@router.get("/projects", response_model=Page[ProjectOut])
def list_projects(
    page: tuple[int, int] = Depends(_page_args), db: Session = Depends(get_db)
) -> Page[ProjectOut]:
    limit, offset = page
    statement = select(Project).order_by(Project.created_at.desc(), Project.id)
    count = select(func.count()).select_from(Project)
    return _paged(db, statement, count, limit, offset, ProjectOut)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> Project:
    return _get_or_404(db, Project, project_id, "Project")


@router.post(
    "/projects/{project_id}/environments",
    response_model=EnvironmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_environment(
    project_id: uuid.UUID, payload: EnvironmentCreate, db: Session = Depends(get_db)
) -> Environment:
    _get_or_404(db, Project, project_id, "Project")
    environment = Environment(
        id=uuid.uuid4(),
        project_id=project_id,
        name=payload.name.strip(),
        description=payload.description,
    )
    db.add(environment)
    _record_audit(
        db, "environment.created", "environment", environment.id, {"name": environment.name}
    )
    _commit(db)
    db.refresh(environment)
    return environment


@router.get("/projects/{project_id}/environments", response_model=Page[EnvironmentOut])
def list_environments(
    project_id: uuid.UUID,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[EnvironmentOut]:
    _get_or_404(db, Project, project_id, "Project")
    limit, offset = page
    statement = (
        select(Environment)
        .where(Environment.project_id == project_id)
        .order_by(Environment.created_at.desc(), Environment.id)
    )
    count = (
        select(func.count()).select_from(Environment).where(Environment.project_id == project_id)
    )
    return _paged(db, statement, count, limit, offset, EnvironmentOut)


@router.post(
    "/environments/{environment_id}/sources",
    response_model=SourceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_source(
    environment_id: uuid.UUID, payload: SourceCreate, db: Session = Depends(get_db)
) -> InfrastructureSource:
    _get_or_404(db, Environment, environment_id, "Environment")
    source = InfrastructureSource(
        id=uuid.uuid4(),
        environment_id=environment_id,
        name=payload.name.strip(),
        provider=payload.provider,
        path=payload.path,
        enabled=payload.enabled,
        configuration=payload.configuration,
    )
    db.add(source)
    _record_audit(
        db,
        "source.created",
        "source",
        source.id,
        {"name": source.name, "provider": source.provider},
    )
    _commit(db)
    db.refresh(source)
    return source


@router.get("/environments/{environment_id}/sources", response_model=Page[SourceOut])
def list_sources(
    environment_id: uuid.UUID,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[SourceOut]:
    _get_or_404(db, Environment, environment_id, "Environment")
    limit, offset = page
    statement = (
        select(InfrastructureSource)
        .where(InfrastructureSource.environment_id == environment_id)
        .order_by(InfrastructureSource.created_at.desc(), InfrastructureSource.id)
    )
    count = (
        select(func.count())
        .select_from(InfrastructureSource)
        .where(InfrastructureSource.environment_id == environment_id)
    )
    return _paged(db, statement, count, limit, offset, SourceOut)


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> InfrastructureSource:
    return _get_or_404(db, InfrastructureSource, source_id, "Infrastructure source")


@router.post(
    "/sources/{source_id}/scans",
    response_model=ScanOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_source_scan(source_id: uuid.UUID, db: Session = Depends(get_db)) -> Scan:
    """Queue a manual scan; the worker runs the provider adapter asynchronously."""
    source = _get_or_404(db, InfrastructureSource, source_id, "Infrastructure source")
    if not source.enabled:
        raise HTTPException(
            status_code=409,
            detail={"code": "source_disabled", "message": "Enable this source before scanning."},
        )
    if source.provider != "opentofu":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "unsupported_provider",
                "message": "Only OpenTofu sources can be scanned in this phase.",
            },
        )
    scan = enqueue_scan(db, source, "manual")
    _record_audit(db, "scan.queued", "scan", scan.id, {"source_id": str(source.id)})
    _commit(db)
    db.refresh(scan)
    return scan


@router.get("/sources/{source_id}/resources", response_model=Page[ResourceOut])
def list_resources(
    source_id: uuid.UUID,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[ResourceOut]:
    _get_or_404(db, InfrastructureSource, source_id, "Infrastructure source")
    limit, offset = page
    statement = (
        select(Resource)
        .where(Resource.source_id == source_id)
        .order_by(Resource.address, Resource.id)
    )
    count = select(func.count()).select_from(Resource).where(Resource.source_id == source_id)
    return _paged(db, statement, count, limit, offset, ResourceOut)


@router.get("/sources/{source_id}/scans", response_model=Page[ScanOut])
def list_source_scans(
    source_id: uuid.UUID,
    scan_status: str | None = Query(default=None, alias="status", max_length=24),
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[ScanOut]:
    _get_or_404(db, InfrastructureSource, source_id, "Infrastructure source")
    limit, offset = page
    filters = [Scan.source_id == source_id]
    if scan_status:
        filters.append(Scan.status == scan_status)
    statement = select(Scan).where(*filters).order_by(Scan.created_at.desc(), Scan.id)
    count = select(func.count()).select_from(Scan).where(*filters)
    return _paged(db, statement, count, limit, offset, ScanOut)


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: uuid.UUID, db: Session = Depends(get_db)) -> Scan:
    return _get_or_404(db, Scan, scan_id, "Scan")


@router.get("/scans/{scan_id}/findings", response_model=Page[FindingOut])
def list_scan_findings(
    scan_id: uuid.UUID,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[FindingOut]:
    _get_or_404(db, Scan, scan_id, "Scan")
    limit, offset = page
    statement = (
        select(DriftFinding)
        .where(DriftFinding.scan_id == scan_id)
        .order_by(DriftFinding.severity, DriftFinding.resource_address)
    )
    count = select(func.count()).select_from(DriftFinding).where(DriftFinding.scan_id == scan_id)
    return _paged(db, statement, count, limit, offset, FindingOut)


@router.get("/findings", response_model=Page[FindingOut])
def list_findings(
    source_id: uuid.UUID | None = None,
    finding_status: str | None = Query(default=None, alias="status", max_length=24),
    severity: str | None = Query(default=None, max_length=24),
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[FindingOut]:
    filters = []
    if source_id is not None:
        filters.append(DriftFinding.source_id == source_id)
    if finding_status:
        filters.append(DriftFinding.status == finding_status)
    if severity:
        filters.append(DriftFinding.severity == severity)
    limit, offset = page
    statement = (
        select(DriftFinding)
        .where(*filters)
        .order_by(DriftFinding.last_seen_at.desc(), DriftFinding.id)
    )
    count = select(func.count()).select_from(DriftFinding).where(*filters)
    return _paged(db, statement, count, limit, offset, FindingOut)


@router.get("/findings/{finding_id}", response_model=FindingDetailOut)
def get_finding(finding_id: uuid.UUID, db: Session = Depends(get_db)) -> FindingDetailOut:
    finding = _get_or_404(db, DriftFinding, finding_id, "Finding")
    changes = db.scalars(
        select(ResourceChange)
        .where(ResourceChange.finding_id == finding_id)
        .order_by(ResourceChange.attribute_path)
    ).all()
    out_changes = [
        ResourceChangeOut(
            id=change.id,
            finding_id=change.finding_id,
            scan_id=change.scan_id,
            attribute_path=change.attribute_path,
            expected_value="[REDACTED]" if change.sensitive else change.expected_value,
            actual_value="[REDACTED]" if change.sensitive else change.actual_value,
            sensitive=change.sensitive,
        )
        for change in changes
    ]
    finding_data = FindingOut.model_validate(finding).model_dump()
    return FindingDetailOut(**finding_data, changes=out_changes)


@router.post(
    "/findings/{finding_id}/github-issue",
    response_model=GitHubIssueOut,
    status_code=status.HTTP_201_CREATED,
)
def create_finding_github_issue(
    finding_id: uuid.UUID, response: Response, db: Session = Depends(get_db)
) -> GitHubIssueOut:
    """Explicitly publish a finding summary to the configured GitHub repository."""
    finding = db.scalar(
        select(DriftFinding).where(DriftFinding.id == finding_id).with_for_update()
    )
    if finding is None:
        _get_or_404(db, DriftFinding, finding_id, "Finding")
    existing = db.scalar(
        select(RemediationProposal)
        .where(
            RemediationProposal.finding_id == finding_id,
            RemediationProposal.status == "github_issue_opened",
        )
        .order_by(RemediationProposal.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        issue_data = existing.proposal
        return GitHubIssueOut(
            proposal_id=existing.id,
            repository=str(issue_data["repository"]),
            issue_number=int(issue_data["issue_number"]),
            issue_url=str(issue_data["issue_url"]),
            created=False,
        )

    changes = db.scalars(
        select(ResourceChange)
        .where(
            ResourceChange.finding_id == finding_id,
            ResourceChange.scan_id == finding.scan_id,
        )
        .order_by(ResourceChange.attribute_path)
    ).all()
    paths = sorted({change.attribute_path.replace("`", "'") for change in changes})
    address = finding.resource_address.replace("`", "'")
    resource_type = finding.resource_type.replace("`", "'")
    title = f"DriftWatch {finding.severity.upper()} drift: {address}"
    body = "\n".join(
        [
            "## Infrastructure drift detected",
            "",
            f"- **Severity:** {finding.severity}",
            f"- **Resource:** `{address}`",
            f"- **Resource type:** `{resource_type}`",
            f"- **Finding ID:** `{finding.id}`",
            f"- **Scan ID:** `{finding.scan_id}`",
            f"- **Source ID:** `{finding.source_id}`",
            "",
            "### Changed attributes",
            *(f"- `{path}`" for path in paths),
            "",
            "### Recommended action",
            finding.recommended_action or "Review the change through the approved IaC workflow.",
            "",
            "Values are omitted from this issue to avoid disclosing infrastructure data.",
            "Created by DriftWatch after an explicit user request.",
        ]
    )
    try:
        issue = create_issue(
            settings.github_repository,
            settings.github_token,
            title,
            body,
        )
    except GitHubIntegrationError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    proposal = RemediationProposal(
        id=uuid.uuid4(),
        finding_id=finding_id,
        status="github_issue_opened",
        proposal={
            "provider": "github",
            "repository": issue.repository,
            "issue_number": issue.number,
            "issue_url": issue.url,
        },
    )
    db.add(proposal)
    _record_audit(
        db,
        "github.issue_created",
        "finding",
        finding_id,
        {"repository": issue.repository, "issue_number": issue.number},
    )
    _commit(db)
    return GitHubIssueOut(
        proposal_id=proposal.id,
        repository=issue.repository,
        issue_number=issue.number,
        issue_url=issue.url,
        created=True,
    )


@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)) -> Policy:
    if payload.project_id is not None:
        _get_or_404(db, Project, payload.project_id, "Project")
    policy = Policy(
        id=uuid.uuid4(),
        project_id=payload.project_id,
        name=payload.name.strip(),
        document=payload.document,
        enabled=payload.enabled,
    )
    db.add(policy)
    _record_audit(db, "policy.created", "policy", policy.id, {"name": policy.name})
    _commit(db)
    db.refresh(policy)
    return policy


@router.get("/policies", response_model=Page[PolicyOut])
def list_policies(
    project_id: uuid.UUID | None = None,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[PolicyOut]:
    filters = [] if project_id is None else [Policy.project_id == project_id]
    limit, offset = page
    statement = select(Policy).where(*filters).order_by(Policy.name, Policy.id)
    count = select(func.count()).select_from(Policy).where(*filters)
    return _paged(db, statement, count, limit, offset, PolicyOut)


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(
    entity_type: str | None = Query(default=None, max_length=64),
    entity_id: uuid.UUID | None = None,
    page: tuple[int, int] = Depends(_page_args),
    db: Session = Depends(get_db),
) -> Page[AuditLogOut]:
    filters = []
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)
    limit, offset = page
    statement = select(AuditLog).where(*filters).order_by(AuditLog.created_at.desc(), AuditLog.id)
    count = select(func.count()).select_from(AuditLog).where(*filters)
    return _paged(db, statement, count, limit, offset, AuditLogOut)
