from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from driftwatch.database import get_db
from driftwatch.db_models import Base
from driftwatch.main import app


@contextmanager
def database_override() -> Generator[None, None, None]:
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
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_health_endpoints() -> None:
    with database_override(), TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_metrics_endpoint_exposes_request_counter() -> None:
    with database_override(), TestClient(app) as client:
        client.get("/health/live")
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "driftwatch_http_requests_total" in response.text
