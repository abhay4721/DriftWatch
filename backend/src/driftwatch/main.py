"""FastAPI application foundation and operational endpoints."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from driftwatch.config import settings
from driftwatch.logging_config import configure_logging

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
HTTP_REQUESTS = Counter("driftwatch_http_requests_total", "HTTP requests served by DriftWatch")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("DriftWatch API starting")
    yield
    logger.info("DriftWatch API stopping")


app = FastAPI(
    title="DriftWatch API",
    description="Local-first infrastructure drift detection platform.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    """Indicate that the API process is running."""
    HTTP_REQUESTS.inc()
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness() -> dict[str, str]:
    """Indicate that the Phase 1 API is ready to accept requests."""
    HTTP_REQUESTS.inc()
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
