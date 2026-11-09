"""Queue claiming and persistence for asynchronous drift scans."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from driftwatch.config import settings
from driftwatch.database import SessionLocal
from driftwatch.db_models import DriftFinding as StoredFinding
from driftwatch.db_models import InfrastructureSource, Resource, ResourceChange, Scan
from driftwatch.drift.adapters.kubernetes import KubernetesAdapter
from driftwatch.drift.adapters.opentofu import OpenTofuAdapter, ScanError, validate_source_path
from driftwatch.drift.engine import DriftEngine
from driftwatch.drift.models import ScanReport
from driftwatch.drift.policy import DriftPolicy

logger = logging.getLogger(__name__)
ScanRunner = Callable[[InfrastructureSource], ScanReport]


def enqueue_scan(db: Session, source: InfrastructureSource, trigger_type: str) -> Scan:
    """Persist a scan request so a worker can claim it after this transaction commits."""
    scan = Scan(
        id=uuid.uuid4(),
        source_id=source.id,
        status="queued",
        trigger_type=trigger_type,
    )
    db.add(scan)
    db.flush()
    return scan


def claim_next_scan(db: Session) -> uuid.UUID | None:
    """Atomically claim one queued scan; PostgreSQL workers skip each other's locks."""
    scan = db.scalar(
        select(Scan)
        .where(Scan.status == "queued")
        .order_by(Scan.created_at, Scan.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if scan is None:
        return None
    scan.status = "running"
    scan.started_at = datetime.now(UTC)
    db.commit()
    return scan.id


def fail_abandoned_scans() -> int:
    """Close scans left running beyond the adapter timeout after a worker restart."""
    stale_before = datetime.now(UTC) - timedelta(seconds=settings.scan_timeout_seconds + 60)
    with SessionLocal() as db:
        result = db.execute(
            update(Scan)
            .where(Scan.status == "running", Scan.started_at < stale_before)
            .values(
                status="failed",
                completed_at=datetime.now(UTC),
                error_code="worker_interrupted",
                error_message="Worker stopped before the scan completed; inspect logs and retry.",
            )
        )
        db.commit()
        return result.rowcount or 0


def run_source_scan(source: InfrastructureSource) -> ScanReport:
    """Run a supported adapter inside the configured source root."""
    if source.provider not in {"opentofu", "kubernetes"}:
        raise ScanError(
            "unsupported_provider",
            f"The {source.provider} scan adapter is not available yet.",
        )
    root = settings.source_root.expanduser().resolve(strict=True)
    configured = Path(source.path).expanduser()
    candidate = configured if configured.is_absolute() else root / configured
    safe_source = validate_source_path(candidate, root)
    policy = DriftPolicy.load()
    if source.provider == "opentofu":
        adapter = OpenTofuAdapter(
            policy,
            source_root=root,
            tofu_path=settings.tofu_path,
            timeout_seconds=settings.scan_timeout_seconds,
        )
    else:
        configuration = source.configuration or {}
        context = configuration.get("context")
        default_namespace = configuration.get("namespace")
        if not isinstance(context, str) or not context.strip():
            raise ScanError(
                "missing_kubernetes_context",
                "Configure an explicit Kubernetes context in the source settings.",
            )
        if default_namespace is not None and not isinstance(default_namespace, str):
            raise ScanError("invalid_kubernetes_namespace", "Kubernetes namespace is invalid.")
        adapter = KubernetesAdapter(
            policy,
            source_root=root,
            context=context,
            kubectl_path=settings.kubectl_path,
            timeout_seconds=settings.scan_timeout_seconds,
            default_namespace=default_namespace,
        )
    report = DriftEngine(adapter).scan(safe_source)
    report.source = safe_source.relative_to(root).as_posix()
    return report


def execute_scan(
    factory: sessionmaker[Session], scan_id: uuid.UUID, runner: ScanRunner = run_source_scan
) -> None:
    """Run a claimed scan and commit its findings and resolution changes."""
    with factory() as db:
        scan = db.get(Scan, scan_id)
        if scan is None or scan.status != "running":
            return
        source = db.get(InfrastructureSource, scan.source_id)
        if source is None:
            _fail(db, scan, "source_missing", "The infrastructure source no longer exists.")
            return
        source_snapshot = InfrastructureSource(
            id=source.id,
            name=source.name,
            provider=source.provider,
            path=source.path,
            enabled=source.enabled,
            configuration=source.configuration,
            environment_id=source.environment_id,
        )

    try:
        report = runner(source_snapshot)
    except ScanError as exc:
        with factory() as db:
            scan = db.get(Scan, scan_id)
            if scan is not None and scan.status == "running":
                _fail(db, scan, exc.code, str(exc))
        return
    except Exception:
        logger.exception("Unexpected scan failure for scan %s", scan_id)
        with factory() as db:
            scan = db.get(Scan, scan_id)
            if scan is not None and scan.status == "running":
                _fail(db, scan, "internal_error", "Scan failed unexpectedly; inspect worker logs.")
        return

    try:
        with factory() as db:
            scan = db.get(Scan, scan_id)
            if scan is None or scan.status != "running":
                return
            now = report.completed_at or datetime.now(UTC)
            scan.status = report.status
            scan.started_at = report.started_at
            scan.completed_at = now
            scan.resource_count = report.resource_count
            scan.error_code = None
            scan.error_message = None
            resource_ids: dict[str, uuid.UUID] = {}
            if report.resources:
                current_resources = db.scalars(
                    select(Resource).where(Resource.source_id == scan.source_id)
                ).all()
                resource_by_address = {resource.address: resource for resource in current_resources}
                current_addresses = {item.address for item in report.resources}
                for item in report.resources:
                    resource = resource_by_address.get(item.address)
                    if resource is None:
                        resource = Resource(
                            id=uuid.uuid4(),
                            source_id=scan.source_id,
                            address=item.address,
                            resource_type=item.resource_type,
                            attributes={},
                            last_seen_at=now,
                        )
                        db.add(resource)
                    else:
                        resource.resource_type = item.resource_type
                        resource.last_seen_at = now
                    resource_ids[item.address] = resource.id
                for resource in current_resources:
                    if resource.address not in current_addresses:
                        db.delete(resource)
                db.flush()

            fingerprints = {item.id for item in report.findings}
            existing = db.scalars(
                select(StoredFinding).where(StoredFinding.source_id == scan.source_id)
            ).all()
            by_fingerprint = {finding.fingerprint: finding for finding in existing}

            for finding in report.findings:
                stored = by_fingerprint.get(finding.id)
                if stored is None:
                    resource_id = resource_ids.get(finding.resource_address)
                    if resource_id is None:
                        resource = db.scalar(
                            select(Resource).where(
                                Resource.source_id == scan.source_id,
                                Resource.address == finding.resource_address,
                            )
                        )
                        resource_id = resource.id if resource else None
                    stored = StoredFinding(
                        id=uuid.uuid4(),
                        scan_id=scan.id,
                        source_id=scan.source_id,
                        resource_id=resource_id,
                        fingerprint=finding.id,
                        resource_address=finding.resource_address,
                        resource_type=finding.resource_type,
                        severity=finding.severity,
                        status="active",
                        recommended_action=finding.recommended_action,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    db.add(stored)
                    by_fingerprint[finding.id] = stored
                else:
                    stored.scan_id = scan.id
                    stored.status = "active"
                    stored.severity = finding.severity
                    stored.resource_address = finding.resource_address
                    stored.resource_type = finding.resource_type
                    stored.recommended_action = finding.recommended_action
                    stored.last_seen_at = now
                    stored.resolved_at = None

                for change in finding.attribute_changes:
                    db.add(
                        ResourceChange(
                            id=uuid.uuid4(),
                            finding_id=stored.id,
                            scan_id=scan.id,
                            attribute_path=change.path,
                            expected_value=change.expected,
                            actual_value=change.actual,
                            sensitive=change.sensitive,
                        )
                    )

            for stored in existing:
                if stored.status == "active" and stored.fingerprint not in fingerprints:
                    stored.status = "resolved"
                    stored.resolved_at = now
                    stored.last_seen_at = now
            db.commit()
    except Exception:
        logger.exception("Could not persist results for scan %s", scan_id)
        with factory() as db:
            scan = db.get(Scan, scan_id)
            if scan is not None and scan.status == "running":
                _fail(db, scan, "persistence_error", "Scan results could not be saved.")


def _fail(db: Session, scan: Scan, code: str, message: str) -> None:
    scan.status = "failed"
    scan.completed_at = datetime.now(UTC)
    scan.error_code = code[:64]
    scan.error_message = message[:2000]
    db.commit()
