import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from driftwatch import scheduler
from driftwatch.config import settings
from driftwatch.db_models import (
    Base,
    Environment,
    InfrastructureSource,
    Notification,
    Project,
    Resource,
    ResourceChange,
    Scan,
)
from driftwatch.db_models import (
    DriftFinding as StoredFinding,
)
from driftwatch.drift.adapters.opentofu import ScanError
from driftwatch.drift.models import AttributeChange, DriftFinding, ScannedResource, ScanReport
from driftwatch.scan_service import claim_next_scan, enqueue_scan, execute_scan


@contextmanager
def database() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def create_source(factory: sessionmaker[Session]) -> InfrastructureSource:
    with factory() as db:
        project = Project(name=f"automation-{uuid.uuid4()}")
        db.add(project)
        db.flush()
        environment = Environment(project_id=project.id, name="test")
        db.add(environment)
        db.flush()
        source = InfrastructureSource(
            environment_id=environment.id,
            name="source",
            provider="opentofu",
            path="tofu-local",
        )
        db.add(source)
        db.commit()
        return source


def report(findings: list[DriftFinding], status: str) -> ScanReport:
    now = datetime.now(UTC)
    return ScanReport(
        provider="opentofu",
        source="tofu-local",
        status=status,
        started_at=now,
        completed_at=now,
        resource_count=1,
        resources=[ScannedResource(address="local_file.example", resource_type="local_file")],
        findings=findings,
    )


def test_worker_persists_findings_and_resolves_them_after_clean_scan() -> None:
    with database() as factory:
        source = create_source(factory)
        drift = DriftFinding(
            id="f" * 64,
            resource_address="local_file.example",
            resource_type="local_file",
            attribute_changes=[
                AttributeChange(
                    path="content",
                    expected="approved",
                    actual="observed",
                    sensitive=False,
                )
            ],
            severity="medium",
            recommended_action="Review the change.",
        )
        with factory() as db:
            queued = enqueue_scan(db, source, "manual")
            db.commit()
            scan_id = queued.id
            assert queued.status == "queued"
            assert queued.trigger_type == "manual"
            assert claim_next_scan(db) == scan_id

        execute_scan(factory, scan_id, runner=lambda _: report([drift], "drift"))
        with factory() as db:
            first_scan = db.get(Scan, scan_id)
            finding = db.scalar(select(StoredFinding).where(StoredFinding.source_id == source.id))
            resource = db.scalar(select(Resource).where(Resource.source_id == source.id))
            assert first_scan is not None and first_scan.status == "drift"
            assert finding is not None and finding.status == "active"
            assert resource is not None and resource.address == "local_file.example"
            assert finding.resource_id == resource.id
            assert db.scalar(select(ResourceChange).where(ResourceChange.finding_id == finding.id))
            notifications = db.scalars(select(Notification)).all()
            assert len(notifications) == 1
            assert notifications[0].payload["type"] == "drift_detected"
            assert "expected" not in notifications[0].payload

            queued_repeat = enqueue_scan(db, db.get(InfrastructureSource, source.id), "scheduled")
            db.commit()
            repeat_scan_id = queued_repeat.id
            assert claim_next_scan(db) == repeat_scan_id

        execute_scan(factory, repeat_scan_id, runner=lambda _: report([drift], "drift"))
        with factory() as db:
            assert len(db.scalars(select(Notification)).all()) == 1
            queued_clean = enqueue_scan(db, db.get(InfrastructureSource, source.id), "scheduled")
            db.commit()
            clean_scan_id = queued_clean.id
            assert claim_next_scan(db) == clean_scan_id

        execute_scan(factory, clean_scan_id, runner=lambda _: report([], "clean"))
        with factory() as db:
            assert db.get(Scan, clean_scan_id).status == "clean"
            resolved = db.scalar(select(StoredFinding).where(StoredFinding.source_id == source.id))
            assert resolved is not None and resolved.status == "resolved"
            assert resolved.resolved_at is not None
            notifications = db.scalars(select(Notification).order_by(Notification.created_at)).all()
            assert len(notifications) == 2
            assert notifications[-1].payload["type"] == "drift_resolved"


def test_worker_records_sanitized_adapter_errors() -> None:
    with database() as factory:
        source = create_source(factory)
        with factory() as db:
            queued = enqueue_scan(db, source, "manual")
            db.commit()
            scan_id = queued.id
            assert claim_next_scan(db) == scan_id

        def fail(_: InfrastructureSource) -> ScanReport:
            raise ScanError("tofu_not_found", "OpenTofu executable was not found.")

        execute_scan(factory, scan_id, runner=fail)
        with factory() as db:
            scan = db.get(Scan, scan_id)
            assert scan is not None
            assert scan.status == "failed"
            assert scan.error_code == "tofu_not_found"
            assert scan.error_message == "OpenTofu executable was not found."
            notification = db.scalar(select(Notification))
            assert notification is not None
            assert notification.payload["type"] == "scan_failed"
            assert notification.payload["error_code"] == "tofu_not_found"


def test_scheduler_enqueues_due_sources_once(monkeypatch) -> None:
    with database() as factory:
        create_source(factory)
        monkeypatch.setattr(scheduler, "SessionLocal", factory)
        monkeypatch.setattr(settings, "schedule_interval_seconds", 3600)

        assert scheduler.enqueue_due_scans() == 1
        assert scheduler.enqueue_due_scans() == 0
        with factory() as db:
            scans = db.scalars(select(Scan)).all()
            assert len(scans) == 1
            assert scans[0].trigger_type == "scheduled"
            assert scans[0].status == "queued"
