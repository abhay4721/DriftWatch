import uuid
from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from driftwatch.database import get_db
from driftwatch.db_models import (
    Base,
    DriftFinding,
    Environment,
    InfrastructureSource,
    Project,
    ResourceChange,
    Scan,
)
from driftwatch.main import app


@contextmanager
def client_with_database() -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_project_environment_source_crud_and_audit() -> None:
    with client_with_database() as (client, _):
        created = client.post("/api/v1/projects", json={"name": "platform", "description": "demo"})
        assert created.status_code == 201
        project = created.json()
        assert project["name"] == "platform"

        listed = client.get("/api/v1/projects?limit=1")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == project["id"]

        environment_response = client.post(
            f"/api/v1/projects/{project['id']}/environments", json={"name": "local"}
        )
        assert environment_response.status_code == 201
        environment = environment_response.json()
        source_response = client.post(
            f"/api/v1/environments/{environment['id']}/sources",
            json={
                "name": "tofu-local",
                "provider": "opentofu",
                "path": "demo/tofu-local",
                "configuration": {"workspace": "default"},
            },
        )
        assert source_response.status_code == 201
        assert source_response.json()["provider"] == "opentofu"

        source_list = client.get(f"/api/v1/environments/{environment['id']}/sources")
        assert source_list.json()["total"] == 1
        audit = client.get("/api/v1/audit-logs?entity_type=source")
        assert audit.json()["total"] == 1
        assert audit.json()["items"][0]["action"] == "source.created"


def test_conflicts_validation_and_missing_resources_use_standard_errors() -> None:
    with client_with_database() as (client, _):
        first = client.post("/api/v1/projects", json={"name": "same"})
        duplicate = client.post("/api/v1/projects", json={"name": "same"})
        invalid = client.post("/api/v1/projects", json={"name": ""})
        missing = client.get(f"/api/v1/projects/{uuid.uuid4()}")

        assert first.status_code == 201
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "conflict"
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


def test_scan_history_and_finding_details_redact_sensitive_values() -> None:
    with client_with_database() as (client, factory):
        with factory() as db:
            project = Project(name="history")
            db.add(project)
            db.flush()
            environment = Environment(project_id=project.id, name="test")
            db.add(environment)
            db.flush()
            source = InfrastructureSource(
                environment_id=environment.id,
                name="source",
                provider="opentofu",
                path="demo/tofu-local",
            )
            db.add(source)
            db.flush()
            scan = Scan(source_id=source.id, status="drift", resource_count=1)
            db.add(scan)
            db.flush()
            finding = DriftFinding(
                scan_id=scan.id,
                source_id=source.id,
                fingerprint="f" * 64,
                resource_address="local_file.secret",
                resource_type="local_file",
                severity="high",
            )
            db.add(finding)
            db.flush()
            change = ResourceChange(
                finding_id=finding.id,
                scan_id=scan.id,
                attribute_path="content",
                expected_value="expected-secret",
                actual_value="actual-secret",
                sensitive=True,
            )
            db.add(change)
            db.commit()
            source_id, scan_id, finding_id = source.id, scan.id, finding.id

        scans = client.get(f"/api/v1/sources/{source_id}/scans?status=drift")
        finding_list = client.get(f"/api/v1/scans/{scan_id}/findings")
        detail = client.get(f"/api/v1/findings/{finding_id}")

        assert scans.json()["total"] == 1
        assert finding_list.json()["items"][0]["resource_address"] == "local_file.secret"
        assert detail.json()["changes"][0]["expected_value"] == "[REDACTED]"
        assert detail.json()["changes"][0]["actual_value"] == "[REDACTED]"


def test_ready_health_check_requires_database() -> None:
    with client_with_database() as (client, _):
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
