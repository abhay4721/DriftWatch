import uuid
from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from driftwatch.database import get_db
from driftwatch.db_models import (
    Base,
    DriftFinding,
    Environment,
    InfrastructureSource,
    Notification,
    Project,
    RemediationProposal,
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
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
            auth = client.post(
                "/api/v1/auth/setup",
                json={"email": "admin@example.com", "password": "Admin-Test-Password-123!"},
            )
            assert auth.status_code == 201
            client.headers.update({"Authorization": f"Bearer {auth.json()['access_token']}"})
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
        scan_response = client.post(f"/api/v1/sources/{source_response.json()['id']}/scans")
        assert scan_response.status_code == 202
        assert scan_response.json()["status"] == "queued"
        assert scan_response.json()["trigger_type"] == "manual"
        kubernetes_source = client.post(
            f"/api/v1/environments/{environment['id']}/sources",
            json={
                "name": "cluster",
                "provider": "kubernetes",
                "path": "kubernetes/staging",
                "configuration": {"context": "docker-desktop", "namespace": "staging"},
            },
        )
        assert kubernetes_source.status_code == 201
        kubernetes_scan = client.post(
            f"/api/v1/sources/{kubernetes_source.json()['id']}/scans"
        )
        assert kubernetes_scan.status_code == 202
        assert kubernetes_scan.json()["status"] == "queued"
        audit = client.get("/api/v1/audit-logs?entity_type=source")
        assert audit.json()["total"] == 2
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


def test_authentication_setup_login_and_role_permissions() -> None:
    with client_with_database() as (client, _):
        assert client.get("/api/v1/auth/setup-status").json() == {"setup_required": False}
        assert client.get("/api/v1/projects").headers["x-frame-options"] == "DENY"
        assert client.post(
            "/api/v1/auth/setup",
            json={"email": "second-admin@example.com", "password": "Another-Password-123!"},
        ).status_code == 409

        invalid = client.post(
            "/api/v1/auth/token",
            json={"email": "admin@example.com", "password": "wrong-password-123"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "invalid_credentials"

        viewer = client.post(
            "/api/v1/auth/users",
            json={
                "email": "viewer@example.com",
                "password": "Viewer-Password-123!",
                "role": "viewer",
            },
        )
        assert viewer.status_code == 201
        assert "password_hash" not in viewer.json()

        login = client.post(
            "/api/v1/auth/token",
            json={"email": "viewer@example.com", "password": "Viewer-Password-123!"},
        )
        assert login.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
        assert client.get("/api/v1/projects").status_code == 200
        denied = client.post("/api/v1/projects", json={"name": "viewer-project"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"

        client.headers.pop("Authorization")
        unauthenticated = client.get("/api/v1/projects")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"


def test_notification_list_count_and_read_actions() -> None:
    with client_with_database() as (client, factory):
        with factory() as db:
            first = Notification(
                channel="in_app",
                status="unread",
                payload={"type": "drift_detected", "title": "Drift detected"},
            )
            second = Notification(
                channel="in_app",
                status="unread",
                payload={"type": "scan_failed", "title": "Scan failed"},
            )
            db.add_all([first, second])
            db.commit()
            first_id = first.id

        assert client.get("/api/v1/notifications/unread-count").json() == {"count": 2}
        unread = client.get("/api/v1/notifications?status=unread")
        assert unread.status_code == 200
        assert unread.json()["total"] == 2
        assert unread.json()["items"][0]["payload"]["title"] in {"Drift detected", "Scan failed"}

        marked = client.post(f"/api/v1/notifications/{first_id}/read")
        assert marked.status_code == 200
        assert marked.json()["status"] == "read"
        assert client.get("/api/v1/notifications/unread-count").json() == {"count": 1}
        bulk = client.post("/api/v1/notifications/read-all")
        assert bulk.json() == {"read_count": 1}
        assert client.get("/api/v1/notifications/unread-count").json() == {"count": 0}


def test_remediation_proposal_requires_review_and_does_not_apply_changes() -> None:
    with client_with_database() as (client, factory):
        with factory() as db:
            project = Project(name="remediation")
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
                fingerprint="p" * 64,
                resource_address="local_file.demo",
                resource_type="local_file",
                severity="medium",
            )
            db.add(finding)
            db.commit()
            finding_id = finding.id

        created = client.post(
            f"/api/v1/findings/{finding_id}/proposals",
            json={
                "title": "Restore declared content",
                "summary": "The observed file content differs from the declared value.",
                "proposed_action": (
                    "Review and update the local file through the approved workflow."
                ),
            },
        )
        assert created.status_code == 201
        proposal = created.json()
        assert proposal["status"] == "proposed"
        proposal_id = proposal["id"]
        assert client.get("/api/v1/proposals?status=proposed").json()["total"] == 1

        approved = client.post(
            f"/api/v1/proposals/{proposal_id}/review",
            json={"decision": "approved", "note": "Reviewed by operations"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["proposal"]["review"]["decision"] == "approved"
        assert client.post(
            f"/api/v1/proposals/{proposal_id}/review", json={"decision": "rejected"}
        ).status_code == 409

        with factory() as db:
            stored = db.get(RemediationProposal, uuid.UUID(proposal_id))
            assert stored is not None
            assert stored.proposal["title"] == "Restore declared content"
            assert stored.branch_name is None
            assert stored.pull_request_url is None


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
