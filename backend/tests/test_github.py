import json
from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from driftwatch import api_v1
from driftwatch.config import settings
from driftwatch.database import get_db
from driftwatch.db_models import (
    AuditLog,
    Base,
    DriftFinding,
    Environment,
    InfrastructureSource,
    Project,
    RemediationProposal,
    ResourceChange,
    Scan,
)
from driftwatch.github import GitHubIssue, create_issue
from driftwatch.main import app


@contextmanager
def github_test_database() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db() -> Generator[Session, None, None]:
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_github_client_posts_issue_with_repository_scoped_token(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps(
                {"number": 17, "html_url": "https://github.com/acme/infra/issues/17"}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("driftwatch.github.urlopen", fake_urlopen)
    result = create_issue("acme/infra", SecretStr("test-token"), "Drift found", "Summary")

    assert result == GitHubIssue("acme/infra", 17, "https://github.com/acme/infra/issues/17")
    assert captured["url"] == "https://api.github.com/repos/acme/infra/issues"
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["body"] == {"title": "Drift found", "body": "Summary"}
    assert captured["timeout"] == 10


def test_finding_issue_is_explicit_sanitized_and_idempotent(monkeypatch) -> None:
    published = []
    monkeypatch.setattr(settings, "github_repository", "acme/infra")
    monkeypatch.setattr(settings, "github_token", SecretStr("test-token"))
    monkeypatch.setattr(
        api_v1,
        "create_issue",
        lambda repository, token, title, body: (
            published.append((repository, token, title, body))
            or GitHubIssue(repository, 17, "https://github.com/acme/infra/issues/17")
        ),
    )

    with github_test_database() as factory:
        with factory() as db:
            project = Project(name="github-project")
            db.add(project)
            db.flush()
            environment = Environment(project_id=project.id, name="prod")
            db.add(environment)
            db.flush()
            source = InfrastructureSource(
                environment_id=environment.id,
                name="infra",
                provider="opentofu",
                path="tofu-local",
            )
            db.add(source)
            db.flush()
            scan = Scan(source_id=source.id, status="drift", resource_count=1)
            db.add(scan)
            db.flush()
            finding = DriftFinding(
                scan_id=scan.id,
                source_id=source.id,
                fingerprint="a" * 64,
                resource_address="local_file.secret",
                resource_type="local_file",
                severity="high",
                recommended_action="Review the change.",
            )
            db.add(finding)
            db.flush()
            db.add(
                ResourceChange(
                    finding_id=finding.id,
                    scan_id=scan.id,
                    attribute_path="content",
                    expected_value="do-not-publish-this-secret",
                    actual_value="also-secret",
                    sensitive=True,
                )
            )
            finding_id = finding.id
            db.commit()

        with TestClient(app) as client:
            first = client.post(f"/api/v1/findings/{finding_id}/github-issue")
            second = client.post(f"/api/v1/findings/{finding_id}/github-issue")

        assert first.status_code == 201
        assert first.json()["created"] is True
        assert first.json()["issue_number"] == 17
        assert second.status_code == 200
        assert second.json()["created"] is False
        assert len(published) == 1
        assert "do-not-publish-this-secret" not in published[0][3]
        assert "also-secret" not in published[0][3]
        assert "content" in published[0][3]

        with factory() as db:
            proposals = db.scalars(select(RemediationProposal)).all()
            audit = db.scalars(select(AuditLog)).all()
            assert len(proposals) == 1
            assert proposals[0].status == "github_issue_opened"
            assert proposals[0].proposal["issue_number"] == 17
            assert len(audit) == 1
            assert audit[0].action == "github.issue_created"
